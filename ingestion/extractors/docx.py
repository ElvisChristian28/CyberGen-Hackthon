"""
extractors/docx.py – DOCX text and hidden-content extraction via python-docx.

Extracts:
- Visible text from paragraphs and tables.
- Runs with white / near-white font colour.
- Runs flagged as hidden (``w:vanish``).
- Tiny-font runs.
- Core document properties (author, title, keywords, etc.).
- Custom properties (if present).
"""

from __future__ import annotations

import io
import logging
from typing import Any, Dict, List, Tuple

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

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


def _is_run_vanished(run: Any) -> bool:
    """Check if the run's XML contains ``<w:vanish />``."""
    rpr = run._element.find(qn("w:rPr"))
    if rpr is not None:
        vanish = rpr.find(qn("w:vanish"))
        if vanish is not None:
            val = vanish.get(qn("w:val"))
            # <w:vanish/> with no val attribute or val="true"/"1" means hidden
            if val is None or val.lower() in ("true", "1", "on"):
                return True
    return False


def extract_docx(
    data: bytes,
    config: IngestionConfig | None = None,
) -> Tuple[str, HiddenContent, List[AuditEntry]]:
    """
    Extract text and hidden content from a DOCX byte-string.

    Parameters
    ----------
    data : bytes
        Raw DOCX file bytes.
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
        doc = Document(io.BytesIO(data))
    except Exception as exc:
        logger.error("Failed to open DOCX: %s", exc)
        audit.append(AuditEntry(stage="parse", status="failed", error=str(exc)))
        return "", hidden, audit

    audit.append(AuditEntry(stage="parse", status="complete"))

    # ── Core properties ──────────────────────────────────────────────────
    try:
        cp = doc.core_properties
        meta_raw: Dict[str, Any] = {}
        for attr in (
            "author",
            "title",
            "subject",
            "keywords",
            "comments",
            "last_modified_by",
            "category",
        ):
            val = getattr(cp, attr, None)
            if val is not None:
                meta_raw[attr] = val
        hidden.metadata = normalize_metadata(meta_raw)
    except Exception as exc:
        logger.warning("DOCX core-properties extraction failed: %s", exc)

    # ── Custom properties ────────────────────────────────────────────────
    try:
        custom_part = doc.custom_xml_parts
        # python-docx ≥ 1.1 may expose custom properties differently;
        # best-effort extraction from the OPC package.
        for part in doc.part.package.parts:
            if hasattr(part, "partname") and "custom" in str(part.partname).lower():
                try:
                    hidden.metadata["_custom_xml"] = part.blob.decode(
                        "utf-8", errors="replace"
                    )
                except Exception:
                    pass
    except Exception:
        pass  # custom properties are optional

    # ── Paragraphs ───────────────────────────────────────────────────────
    visible_parts: List[str] = []

    for para_idx, para in enumerate(doc.paragraphs):
        para_label = f"[para {para_idx + 1}]"
        for run in para.runs:
            text = run.text
            if not text:
                continue

            is_hidden_run = False

            # 1. Vanish check (hidden text)
            if _is_run_vanished(run):
                hidden.invisible_spans.append(
                    f"{para_label} hidden (vanish): {text.strip()}"
                )
                is_hidden_run = True

            # 2. Colour check
            try:
                rgb: RGBColor | None = run.font.color.rgb
                if rgb is not None:
                    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
                    if is_near_white(r, g, b, cfg.near_white_threshold):
                        hidden.invisible_spans.append(
                            f"{para_label} white text: {text.strip()}"
                        )
                        is_hidden_run = True
            except Exception:
                pass  # colour not set → visible

            # 3. Tiny font
            try:
                font_size = run.font.size
                if font_size is not None:
                    size_pt = font_size.pt
                    if is_tiny_font(size_pt, cfg.tiny_font_pt):
                        hidden.invisible_spans.append(
                            f"{para_label} tiny font ({size_pt:.1f}pt): {text.strip()}"
                        )
                        is_hidden_run = True
            except Exception:
                pass

            if not is_hidden_run:
                visible_parts.append(text)

    # ── Tables ───────────────────────────────────────────────────────────
    for table_idx, table in enumerate(doc.tables):
        for row in table.rows:
            for cell in row.cells:
                cell_text = cell.text.strip()
                if cell_text:
                    visible_parts.append(cell_text)

    raw_text = "\n".join(visible_parts)

    # ── Zero-width scan ──────────────────────────────────────────────────
    zw_hits = scan_zero_width(raw_text, context_chars=cfg.context_chars)
    hidden.zero_width.extend(zw_hits)
    raw_text = strip_zero_width(raw_text)

    audit.append(AuditEntry(stage="hidden_scan", status="complete"))
    return raw_text, hidden, audit
