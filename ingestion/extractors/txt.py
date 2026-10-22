"""
extractors/txt.py – Plain-text extraction with encoding detection and zero-width scan.

Tries UTF-8, UTF-8-SIG, UTF-16, and Latin-1 (with ``errors='replace'``)
in that order.  Scans for zero-width characters and strips them from the
returned visible text.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

from ingestion.hidden import (
    AuditEntry,
    HiddenContent,
    IngestionConfig,
    scan_zero_width,
    strip_zero_width,
    normalize_metadata,
)

logger = logging.getLogger(__name__)

_ENCODINGS = ["utf-8", "utf-8-sig", "utf-16", "latin-1"]


def _try_decode(data: bytes) -> str:
    """Attempt to decode *data* with several encodings; last resort uses replace."""
    for enc in _ENCODINGS:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    # Absolute fallback
    return data.decode("latin-1", errors="replace")


def extract_txt(
    data: bytes,
    config: IngestionConfig | None = None,
) -> Tuple[str, HiddenContent, List[AuditEntry]]:
    """
    Extract text and hidden content from a plain-text byte-string.

    Parameters
    ----------
    data : bytes
        Raw file bytes.
    config : IngestionConfig, optional
        Tunable thresholds.

    Returns
    -------
    (raw_text, hidden_content, audit_entries)
    """
    cfg = config or IngestionConfig()
    hidden = HiddenContent()
    audit: List[AuditEntry] = []

    try:
        text = _try_decode(data)
    except Exception as exc:
        logger.error("Failed to decode text: %s", exc)
        audit.append(AuditEntry(stage="parse", status="failed", error=str(exc)))
        return "", hidden, audit

    audit.append(AuditEntry(stage="parse", status="complete"))

    # ── Zero-width scan ──────────────────────────────────────────────────
    zw_hits = scan_zero_width(text, context_chars=cfg.context_chars)
    hidden.zero_width.extend(zw_hits)
    raw_text = strip_zero_width(text)

    audit.append(AuditEntry(stage="hidden_scan", status="complete"))
    return raw_text, hidden, audit
