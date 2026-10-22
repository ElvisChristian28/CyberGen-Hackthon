"""
loader.py – Format dispatcher and CLI entry point for the ingestion pipeline.

Public API
----------
``ingest(path_or_bytes, filename=None, config=None) -> dict``

CLI
---
``python -m ingestion.loader <path>``
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import zipfile
from typing import Any, Dict, Optional, Union

from ingestion.chunker import chunk_text, clean_text
from ingestion.hidden import (
    AuditEntry,
    HiddenContent,
    IngestionConfig,
    IngestionResult,
    validate_output,
)
from ingestion.extractors.pdf import extract_pdf
from ingestion.extractors.docx import extract_docx
from ingestion.extractors.html import extract_html
from ingestion.extractors.txt import extract_txt

logger = logging.getLogger(__name__)

# ── Magic-byte sniffing ─────────────────────────────────────────────────────

_HTML_HINTS = (b"<html", b"<!doctype", b"<body", b"<div", b"<head", b"<p ", b"<p>", b"<span", b"<table")


def _sniff_format(data: bytes, filename: str) -> str:
    """
    Detect file format by magic bytes/content, falling back to extension.

    Returns one of: ``'pdf'``, ``'docx'``, ``'html'``, ``'txt'``.
    """
    # PDF magic
    if data[:5] == b"%PDF-":
        return "pdf"

    # DOCX: ZIP with word/ directory
    if data[:4] == b"PK\x03\x04":
        try:
            import io
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                if any(n.startswith("word/") for n in names):
                    return "docx"
        except (zipfile.BadZipFile, Exception):
            pass

    # HTML heuristic: check first 4 KB for HTML-like tags
    head = data[:4096].lower().lstrip()
    for hint in _HTML_HINTS:
        if hint in head:
            return "html"

    # Extension fallback
    ext = pathlib.Path(filename).suffix.lower() if filename else ""
    ext_map = {".pdf": "pdf", ".docx": "docx", ".html": "html", ".htm": "html"}
    if ext in ext_map:
        return ext_map[ext]

    return "txt"


# ── Public API ───────────────────────────────────────────────────────────────

def ingest(
    path_or_bytes: Union[str, pathlib.Path, bytes],
    filename: Optional[str] = None,
    config: Optional[IngestionConfig] = None,
) -> Dict[str, Any]:
    """
    Ingest a document and return the structured output contract as a dict.

    Parameters
    ----------
    path_or_bytes : str | Path | bytes
        File path or raw bytes of the document.
    filename : str, optional
        Original filename (used for extension fallback and the output).
        Inferred from *path_or_bytes* when it is a path.
    config : IngestionConfig, optional
        Pipeline configuration / thresholds.

    Returns
    -------
    dict
        The output contract, ready for ``detection.scanner.scan(obj)``.
    """
    cfg = config or IngestionConfig()

    # ── Read bytes ───────────────────────────────────────────────────────
    if isinstance(path_or_bytes, (str, pathlib.Path)):
        fpath = pathlib.Path(path_or_bytes)
        if filename is None:
            filename = fpath.name
        try:
            data = fpath.read_bytes()
        except Exception as exc:
            logger.error("Cannot read file %s: %s", fpath, exc)
            result = IngestionResult(filename=filename or str(fpath))
            result.audit_trail.append(
                AuditEntry(stage="ingestion", status="failed", error=str(exc))
            )
            return result.to_dict()
    else:
        data = path_or_bytes
        if filename is None:
            filename = "unknown"

    # ── Size check ───────────────────────────────────────────────────────
    if len(data) > cfg.max_file_size:
        logger.warning("File %s exceeds max size (%d > %d)", filename, len(data), cfg.max_file_size)
        result = IngestionResult(filename=filename)
        result.audit_trail.append(
            AuditEntry(
                stage="ingestion",
                status="failed",
                error=f"File size {len(data)} exceeds limit {cfg.max_file_size}",
            )
        )
        return result.to_dict()

    # ── Empty file ───────────────────────────────────────────────────────
    if len(data) == 0:
        logger.info("Empty file: %s", filename)
        result = IngestionResult(filename=filename)
        result.audit_trail.append(
            AuditEntry(stage="ingestion", status="failed", error="Empty file")
        )
        return result.to_dict()

    # ── Format dispatch ──────────────────────────────────────────────────
    fmt = _sniff_format(data, filename)
    logger.info("Detected format '%s' for %s", fmt, filename)

    extractors = {
        "pdf": extract_pdf,
        "docx": extract_docx,
        "html": extract_html,
        "txt": extract_txt,
    }

    extractor = extractors[fmt]

    try:
        raw_text, hidden, audit_entries = extractor(data, config=cfg)
    except Exception as exc:
        # Extractor should never raise, but defence-in-depth
        logger.error("Extractor '%s' crashed: %s", fmt, exc)
        result = IngestionResult(filename=filename)
        result.audit_trail.append(
            AuditEntry(stage="parse", status="failed", error=f"Extractor crash: {exc}")
        )
        result.audit_trail.append(
            AuditEntry(stage="ingestion", status="failed", error=str(exc))
        )
        return result.to_dict()

    # ── Build result ─────────────────────────────────────────────────────
    result = IngestionResult(
        filename=filename,
        raw_text=raw_text,
        hidden_content=hidden,
        audit_trail=audit_entries,
    )

    # Check if parsing failed (no text and a failed audit entry)
    parse_failed = any(
        e.status == "failed" and e.stage == "parse" for e in result.audit_trail
    )

    # ── Chunking ─────────────────────────────────────────────────────────
    if raw_text and not parse_failed:
        cleaned = clean_text(raw_text)
        chunks = chunk_text(
            cleaned,
            target_tokens=cfg.chunk_target_tokens,
            overlap_tokens=cfg.chunk_overlap_tokens,
        )
        result.chunks = chunks
        result.audit_trail.append(AuditEntry(stage="chunk", status="complete"))
    else:
        result.audit_trail.append(
            AuditEntry(
                stage="chunk",
                status="skipped",
                error="No visible text to chunk" if not parse_failed else "Parse failed",
            )
        )

    # ── Final audit entry ────────────────────────────────────────────────
    overall_status = "failed" if parse_failed else "complete"
    result.audit_trail.append(AuditEntry(stage="ingestion", status=overall_status))

    return result.to_dict()


# ── CLI entry point ──────────────────────────────────────────────────────────

def main() -> None:
    """CLI: ``python -m ingestion.loader <path>``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m ingestion.loader <file_path>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    if not os.path.isfile(path):
        print(f"Error: {path} is not a file", file=sys.stderr)
        sys.exit(1)

    result = ingest(path)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
