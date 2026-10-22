"""
scripts/ingest_and_detect.py
============================
End-to-end runner: file → Ingestion → validate → adapter → Detection → validate
→ write scored JSON → print one-line summary.

Usage
-----
    # Full pipeline (ingestion + detection)
    python -m scripts.ingest_and_detect samples/clean_report.txt

    # Custom output path
    python -m scripts.ingest_and_detect samples/injected_doc.txt --out /tmp/out.json

    # Ingestion only (saves .ingested.json, skips detection)
    python -m scripts.ingest_and_detect samples/clean_report.txt --ingest-only

    # Process all files in samples/
    python -m scripts.ingest_and_detect samples/

    # Verbose logging
    python -m scripts.ingest_and_detect samples/clean_report.txt --verbose
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Output directory ─────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT_DIR = _REPO_ROOT / "samples" / "out"


def _out_path(stem: str, suffix: str) -> Path:
    """Return samples/out/<stem>.<suffix>"""
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    return _OUT_DIR / f"{stem}.{suffix}"


def process_file(
    file_path: Path,
    *,
    ingest_only: bool = False,
    out_path: Path | None = None,
    use_presidio: bool = True,
) -> dict:
    """
    Run one file through the full (or partial) pipeline.

    Returns the final document dict (ingested or scored).
    Writes the output to *out_path* (or the default samples/out/ location).
    Prints a one-line summary to stdout.
    """
    from ingestion.loader import ingest
    from shared.schema import validate_ingested, validate_scored
    from shared.adapters import ingestion_to_detection

    stem = file_path.stem

    # ── Step 1: Ingestion ────────────────────────────────────────────────
    logger.info("Ingesting: %s", file_path)
    ingested = ingest(str(file_path))

    # ── Step 2: Validate ingestion output ────────────────────────────────
    try:
        validate_ingested(ingested)
    except ValueError as exc:
        logger.error("Ingestion validation failed for %s: %s", file_path.name, exc)
        raise

    if ingest_only:
        dest = out_path or _out_path(stem, "ingested.json")
        dest.write_text(json.dumps(ingested, indent=2, ensure_ascii=False), encoding="utf-8")
        n_chunks = len(ingested.get("chunks", []))
        print(f"[INGESTED] {file_path.name} | chunks={n_chunks} | → {dest}")
        return ingested

    # ── Step 3: Adapter ──────────────────────────────────────────────────
    detection_input = ingestion_to_detection(ingested)

    # ── Step 4: Detection ────────────────────────────────────────────────
    from detection.scanner import scan
    logger.info("Scanning: %s", file_path.name)
    scored = scan(detection_input, use_presidio=use_presidio)

    # ── Step 5: Validate scored output ───────────────────────────────────
    try:
        validate_scored(scored)
    except ValueError as exc:
        logger.error("Detection validation failed for %s: %s", file_path.name, exc)
        raise

    # ── Step 6: Write output ─────────────────────────────────────────────
    dest = out_path or _out_path(stem, "scored.json")
    dest.write_text(json.dumps(scored, indent=2, ensure_ascii=False), encoding="utf-8")

    n_findings = len(scored.get("findings", []))
    risk = scored.get("risk_score", 0)
    print(
        f"[SCORED]   {file_path.name} | "
        f"risk_score={risk} | "
        f"findings={n_findings} | "
        f"→ {dest}"
    )
    return scored


def _collect_files(path: Path) -> list[Path]:
    """Return a sorted list of processable files from a file or directory."""
    if path.is_file():
        return [path]
    exts = {".txt", ".pdf", ".docx", ".html", ".htm"}
    return sorted(f for f in path.iterdir() if f.suffix.lower() in exts and f.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.ingest_and_detect",
        description="Run a file through Ingestion → Detection and save the scored JSON.",
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Path to a single document or a directory of documents.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write output to this path (single-file mode only).",
    )
    parser.add_argument(
        "--ingest-only",
        action="store_true",
        help="Stop after ingestion; save .ingested.json and skip detection.",
    )
    parser.add_argument(
        "--no-presidio",
        action="store_true",
        help="Disable Presidio NLP-based PII detection (faster, regex-only).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    files = _collect_files(args.path)
    if not files:
        print(f"No processable files found at: {args.path}", file=sys.stderr)
        sys.exit(1)

    if args.out and len(files) > 1:
        print("--out cannot be used with a directory (multiple files).", file=sys.stderr)
        sys.exit(1)

    errors = 0
    for fpath in files:
        try:
            process_file(
                fpath,
                ingest_only=args.ingest_only,
                out_path=args.out if len(files) == 1 else None,
                use_presidio=not args.no_presidio,
            )
        except Exception as exc:
            print(f"[ERROR]    {fpath.name}: {exc}", file=sys.stderr)
            errors += 1

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
