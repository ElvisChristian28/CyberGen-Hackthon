"""
pipeline/audit.py

Append-only, thread-safe JSONL audit log.

Every stage in the pipeline writes one record per significant event so the
dashboard can reconstruct a document's complete journey.

Record schema
-------------
{
    "doc_id":     str,
    "filename":   str,
    "stage":      str,   # e.g. "pipeline", "quarantine"
    "decision":   str | None,
    "risk_score": int | None,
    "ts":         str,   # ISO-8601 UTC
    ...extra fields passed via **kwargs
}

Usage
-----
    from pipeline.audit import log, read_all, read_for_doc

    log("abc123", "report.pdf", stage="pipeline", decision="SANITIZE", risk_score=55)
    records = read_all()
    doc_records = read_for_doc("abc123")
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pipeline.config import cfg

logger = logging.getLogger(__name__)

# One process-wide lock so concurrent threads don't interleave writes.
_write_lock = threading.Lock()


_AUDIT_FILE_OVERRIDE: Optional[Path] = None


def set_audit_file(path: Optional[Path]) -> None:
    """Override the audit file path (used by tests for isolation)."""
    global _AUDIT_FILE_OVERRIDE
    _AUDIT_FILE_OVERRIDE = path


def _audit_path() -> Path:
    """Return the configured audit file path, creating parent dirs if needed."""
    if _AUDIT_FILE_OVERRIDE is not None:
        _AUDIT_FILE_OVERRIDE.parent.mkdir(parents=True, exist_ok=True)
        return _AUDIT_FILE_OVERRIDE
    cfg.ensure_data_dir()
    return cfg.AUDIT_FILE


def _utc_now() -> str:
    """Return the current time as an ISO-8601 UTC string."""
    return datetime.now(timezone.utc).isoformat()


def log(
    doc_id: str,
    filename: str,
    *,
    stage: str,
    decision: Optional[str] = None,
    risk_score: Optional[int] = None,
    **extra: Any,
) -> None:
    """
    Append one audit record to the JSONL log.

    Parameters
    ----------
    doc_id:     Unique document identifier.
    filename:   Original filename for human readability.
    stage:      Pipeline stage name (e.g. ``"pipeline"``, ``"quarantine"``).
    decision:   ALLOW | SANITIZE | QUARANTINE | APPROVED | REJECTED | None.
    risk_score: Integer 0-100 from the Detection stage, or None.
    **extra:    Any additional key-value pairs merged into the record.
    """
    record: dict[str, Any] = {
        "doc_id": doc_id,
        "filename": filename,
        "stage": stage,
        "decision": decision,
        "risk_score": risk_score,
        "ts": _utc_now(),
    }
    record.update(extra)

    path = _audit_path()
    with _write_lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()

    logger.debug("Audit record written: stage=%s doc_id=%s decision=%s", stage, doc_id, decision)


def read_all() -> list[dict[str, Any]]:
    """
    Return all audit records as a list of dicts, oldest first.

    Returns an empty list if the audit file does not yet exist.
    """
    path = _audit_path()
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed audit line %d in %s", lineno, path)

    return records


def read_for_doc(doc_id: str) -> list[dict[str, Any]]:
    """
    Return all audit records for a specific document, oldest first.

    Parameters
    ----------
    doc_id: The document identifier to filter on.
    """
    return [r for r in read_all() if r.get("doc_id") == doc_id]
