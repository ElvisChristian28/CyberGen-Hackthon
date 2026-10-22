"""
extractors/pdf.py – PDF text and hidden-content extraction via PyMuPDF.

Extracts:
- Visible text (page by page).
- White / near-white spans.
- Tiny-font spans (< configurable threshold).
- Spans positioned outside the page bounding box.
- Document metadata (info dict) and XMP metadata.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

import fitz  # PyMuPDF

from ingestion.hidden import (
    AuditEntry,
    HiddenContent,
    IngestionConfig,
    is_near_white,
    is_tiny_font,
    normalize_metadata,
    scan_zero_width,
    strip_zero_width,
)

logger = logging.getLogger(__name__)


def extract_pdf(
    data: bytes,
    config: IngestionConfig | None = None,
) -> Tuple[str, HiddenContent, List[AuditEntry]]:
    """
    Extract text and hidden content from a PDF byte-string.

    Parameters
    ----------
    data : bytes
        Raw PDF file bytes.
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
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        logger.error("Failed to open PDF: %s", exc)
        audit.append(AuditEntry(stage="parse", status="failed", error=str(exc)))
        return "", hidden, audit

    # Check if encrypted and unreadable
    if doc.is_encrypted:
        logger.warning("PDF is encrypted; attempting without password.")
        try:
            if not doc.authenticate(""):
                audit.append(
                    AuditEntry(
                        stage="parse",
                        status="failed",
                        error="Encrypted PDF – could not authenticate",
                    )
                )
                doc.close()
                return "", hidden, audit
        except Exception as exc:
            audit.append(
                AuditEntry(stage="parse", status="failed", error=str(exc))
            )
            doc.close()
            return "", hidden, audit

    audit.append(AuditEntry(stage="parse", status="complete"))

    # ── Metadata ─────────────────────────────────────────────────────────
    try:
        raw_meta: Dict[str, Any] = dict(doc.metadata) if doc.metadata else {}
        # Try XMP
        try:
            xmp = doc.get_xml_metadata()
            if xmp:
                raw_meta["_xmp_raw"] = xmp
        except Exception:
            pass
        hidden.metadata = normalize_metadata(raw_meta)
    except Exception as exc:
        logger.warning("Metadata extraction failed: %s", exc)

    # ── Per-page extraction ──────────────────────────────────────────────
    visible_parts: List[str] = []

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_label = f"[page {page_idx + 1}]"

        try:
            page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        except Exception as exc:
            logger.warning("Could not extract text on page %d: %s", page_idx + 1, exc)
            audit.append(
                AuditEntry(
                    stage="parse",
                    status="warning",
                    error=f"No extractable text on page {page_idx + 1}: {exc}",
                )
            )
            continue

        page_rect = page.rect  # page bounding box
        page_has_text = False

        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:  # 0 = text block
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text: str = span.get("text", "")
                    if not text.strip():
                        continue

                    page_has_text = True
                    is_hidden = False

                    # 1. Colour check (near-white)
                    color_int = span.get("color", 0)
                    r = (color_int >> 16) & 0xFF
                    g = (color_int >> 8) & 0xFF
                    b = color_int & 0xFF
                    if is_near_white(r, g, b, cfg.near_white_threshold):
                        hidden.invisible_spans.append(
                            f"{page_label} white text: {text.strip()}"
                        )
                        is_hidden = True

                    # 2. Tiny font
                    font_size: float = span.get("size", 12.0)
                    if is_tiny_font(font_size, cfg.tiny_font_pt):
                        hidden.invisible_spans.append(
                            f"{page_label} tiny font ({font_size:.1f}pt): {text.strip()}"
                        )
                        is_hidden = True

                    # 3. Off-page position
                    span_bbox = fitz.Rect(span.get("bbox", (0, 0, 0, 0)))
                    if not page_rect.intersects(span_bbox):
                        hidden.invisible_spans.append(
                            f"{page_label} off-page span: {text.strip()}"
                        )
                        is_hidden = True

                    if not is_hidden:
                        visible_parts.append(text)

        if not page_has_text:
            audit.append(
                AuditEntry(
                    stage="parse",
                    status="info",
                    error=f"No extractable text on page {page_idx + 1}",
                )
            )

    doc.close()

    raw_text = "".join(visible_parts)

    # ── Zero-width scan ──────────────────────────────────────────────────
    zw_hits = scan_zero_width(raw_text, context_chars=cfg.context_chars)
    hidden.zero_width.extend(zw_hits)
    raw_text = strip_zero_width(raw_text)

    # Also scan invisible spans for zero-width
    for span_text in list(hidden.invisible_spans):
        zw = scan_zero_width(span_text, context_chars=cfg.context_chars)
        hidden.zero_width.extend(zw)

    audit.append(AuditEntry(stage="hidden_scan", status="complete"))
    return raw_text, hidden, audit
