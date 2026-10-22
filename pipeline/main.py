"""
pipeline/main.py

Pipeline orchestrator — the single entry point that ties together:
  Detection output → Decision → Sanitization → Vector DB → Quarantine → Audit

Public API
----------
    from pipeline.main import process_document

    result = process_document(scored_doc)

CLI
---
    # Process a single pre-scored document
    python -m pipeline.main --input path/to/scored_doc.json

    # Run all fixture documents and print a summary table
    python -m pipeline.main --test-all

    # Run with verbose logging
    python -m pipeline.main --test-all --verbose
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_doc(doc: dict[str, Any]) -> None:
    """
    Raise ValueError if the incoming document is missing required fields.

    Required: doc_id, filename, raw_text, chunks, findings, risk_score, reasons.
    """
    required = ["doc_id", "filename", "raw_text", "chunks", "findings", "risk_score", "reasons"]
    missing = [k for k in required if k not in doc]
    if missing:
        raise ValueError(f"Document is missing required fields: {missing}")

    risk_score = doc["risk_score"]
    if not isinstance(risk_score, int) or not (0 <= risk_score <= 100):
        raise ValueError(
            f"risk_score must be an int in [0, 100], got {risk_score!r}"
        )


# ── Core processing function ──────────────────────────────────────────────────

def process_document(
    doc: dict[str, Any],
    quarantine_file: Optional[Path] = None,
    persist_dir: Optional[Path] = None,
    audit_file: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Run a fully-scored document through the Pipeline stage.

    Steps
    -----
    1. Validate the input.
    2. Decide: ALLOW / SANITIZE / QUARANTINE based on risk_score.
    3. Execute the decision:
       - ALLOW     → ingest raw chunks into protected DB.
       - SANITIZE  → sanitize text + chunks, ingest sanitized chunks into
                     protected DB, preserve raw_text unchanged.
       - QUARANTINE → add to quarantine, do NOT touch the protected DB.
    4. Always ingest raw chunks into the unprotected DB (demo contrast).
    5. Append a pipeline entry to the document's audit_trail list.
    6. Write audit log records.
    7. Set ``in_vector_db`` and ``in_quarantine`` flags.

    Parameters
    ----------
    doc:
        Scored DocumentPayload dict from the Detection stage.
    quarantine_file:
        Override quarantine store path (used by tests).
    persist_dir:
        Override ChromaDB directory (used by tests).

    Returns
    -------
    dict
        Updated DocumentPayload with pipeline fields populated.

    Raises
    ------
    ValueError
        If the document fails validation.  Other internal errors are caught,
        logged, and re-raised (so batch processing in --test-all can continue).
    """
    # Lazy imports to avoid circular dependencies at module load
    from pipeline.audit import log as audit_log, set_audit_file
    from pipeline.decision import decide
    from pipeline.sanitizer import sanitize, sanitize_chunks
    from pipeline.vector_db import ingest_to_vector_db, ingest_unprotected
    from pipeline import quarantine as q_store

    # Thread the test-override audit file path through (no-op in production)
    set_audit_file(audit_file)

    # Work on a deep copy so the caller's dict is never mutated
    result = copy.deepcopy(doc)

    # ── 1. Validate ───────────────────────────────────────────────────────
    _validate_doc(result)
    doc_id = result["doc_id"]
    filename = result["filename"]

    audit_log(doc_id, filename, stage="pipeline", status="received",
              risk_score=result["risk_score"])
    logger.info("Processing doc_id=%s (%s) risk_score=%d",
                doc_id, filename, result["risk_score"])

    # ── 2. Decide ─────────────────────────────────────────────────────────
    decision = decide(result["risk_score"])
    result["decision"] = decision
    result.setdefault("sanitized_text", None)
    result.setdefault("in_vector_db", False)
    result.setdefault("in_quarantine", False)

    audit_log(doc_id, filename, stage="pipeline", decision=decision,
              risk_score=result["risk_score"])
    logger.info("Decision for doc_id=%s → %s", doc_id, decision)

    # Keep a snapshot of the raw chunks BEFORE any sanitization so the
    # unprotected DB always receives original content.
    result["raw_chunks"] = copy.deepcopy(result["chunks"])

    # ── 3. Execute decision ───────────────────────────────────────────────
    if decision == "ALLOW":
        ingest_to_vector_db(result, protected=True, persist_dir=persist_dir)
        result["in_vector_db"] = True
        audit_log(doc_id, filename, stage="pipeline", status="ingested_protected",
                  decision=decision)

    elif decision == "SANITIZE":
        # Sanitize both the full text (for dashboard before/after view) and chunks
        result["sanitized_text"] = sanitize(result["raw_text"], result["findings"])
        result["chunks"] = sanitize_chunks(result["chunks"], result["findings"])

        audit_log(doc_id, filename, stage="pipeline", status="sanitized",
                  decision=decision)
        logger.info("Sanitized doc_id=%s; ingesting sanitized chunks", doc_id)

        # Ingest the sanitized chunks (result["chunks"] is now redacted)
        ingest_to_vector_db(result, protected=True, persist_dir=persist_dir)
        result["in_vector_db"] = True
        audit_log(doc_id, filename, stage="pipeline", status="ingested_protected",
                  decision=decision)

    elif decision == "QUARANTINE":
        q_store.add(result, quarantine_file=quarantine_file)
        result["in_quarantine"] = True
        # Do NOT ingest into the protected DB
        audit_log(doc_id, filename, stage="pipeline", status="quarantined",
                  decision=decision)
        logger.info("Quarantined doc_id=%s — NOT ingested to protected DB", doc_id)

    # ── 4. Ingest raw chunks to unprotected DB (always) ───────────────────
    ingest_unprotected(result, persist_dir=persist_dir)
    audit_log(doc_id, filename, stage="pipeline", status="ingested_unprotected",
              decision=decision)

    # ── 5. Append pipeline entry to the document's own audit_trail ────────
    pipeline_entry = {
        "stage": "pipeline",
        "decision": decision,
        "in_vector_db": result["in_vector_db"],
        "in_quarantine": result["in_quarantine"],
        "ts": _utc_now(),
    }
    result.setdefault("audit_trail", [])
    result["audit_trail"].append(pipeline_entry)

    # Clean up the temporary raw_chunks key before returning
    result.pop("raw_chunks", None)

    logger.info(
        "Done: doc_id=%s decision=%s in_vector_db=%s in_quarantine=%s",
        doc_id, decision, result["in_vector_db"], result["in_quarantine"],
    )
    return result


# ── Batch / fixture runner ────────────────────────────────────────────────────

def _run_fixtures(
    fixture_dir: Path,
    quarantine_file: Optional[Path] = None,
    persist_dir: Optional[Path] = None,
    audit_file: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Process every JSON fixture in *fixture_dir* and return summary rows."""
    fixtures = sorted(fixture_dir.glob("*.json"))
    if not fixtures:
        print(f"No fixture files found in {fixture_dir}")
        return []

    rows = []
    for path in fixtures:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            result = process_document(
                doc,
                quarantine_file=quarantine_file,
                persist_dir=persist_dir,
                audit_file=audit_file,
            )
            rows.append({
                "filename": result["filename"],
                "score": result["risk_score"],
                "decision": result["decision"],
                "in_vector_db": result["in_vector_db"],
                "in_quarantine": result["in_quarantine"],
                "error": None,
            })
        except Exception as exc:
            logger.exception("Failed to process fixture %s", path.name)
            # Load just enough to show in summary
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                fname = raw.get("filename", path.name)
                score = raw.get("risk_score", "?")
            except Exception:
                fname = path.name
                score = "?"
            rows.append({
                "filename": fname,
                "score": score,
                "decision": "ERROR",
                "in_vector_db": False,
                "in_quarantine": False,
                "error": str(exc),
            })
    return rows


def _print_summary(rows: list[dict[str, Any]]) -> None:
    """Print a formatted summary table to stdout."""
    col_w = [28, 7, 12, 12, 13, 40]
    header = ["Filename", "Score", "Decision", "in_vector_db", "in_quarantine", "Error"]
    sep = "  ".join("-" * w for w in col_w)

    def row_str(r: dict) -> str:
        cells = [
            str(r.get("filename", ""))[:col_w[0]],
            str(r.get("score", ""))[:col_w[1]],
            str(r.get("decision", ""))[:col_w[2]],
            str(r.get("in_vector_db", ""))[:col_w[3]],
            str(r.get("in_quarantine", ""))[:col_w[4]],
            str(r.get("error") or "")[:col_w[5]],
        ]
        return "  ".join(c.ljust(w) for c, w in zip(cells, col_w))

    print()
    print(row_str({k: v for k, v in zip(header, header)}))
    print(sep)
    for r in rows:
        print(row_str(r))
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.main",
        description="CyberGen Pipeline — process scored documents.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input", "-i",
        type=Path,
        metavar="PATH",
        help="Path to a single scored document JSON file",
    )
    group.add_argument(
        "--test-all",
        action="store_true",
        help="Process all fixture documents in pipeline/tests/fixtures/ and print a summary",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.input:
        try:
            doc = json.loads(args.input.read_text(encoding="utf-8"))
            result = process_document(doc)
            print(json.dumps(
                {k: result[k] for k in
                 ["doc_id", "filename", "risk_score", "decision",
                  "in_vector_db", "in_quarantine", "reasons"]},
                indent=2,
            ))
        except Exception as exc:
            logger.error("Failed: %s", exc)
            sys.exit(1)

    elif args.test_all:
        fixture_dir = Path(__file__).parent / "tests" / "fixtures"
        rows = _run_fixtures(fixture_dir)
        _print_summary(rows)


if __name__ == "__main__":
    _cli()
