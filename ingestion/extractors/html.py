"""
extractors/html.py – HTML text and hidden-content extraction via BeautifulSoup.

Extracts:
- Visible text (excluding hidden elements and comments).
- HTML comments (``<!-- ... -->``).
- Elements hidden via CSS (display:none, visibility:hidden, opacity:0,
  font-size:0, color:#fff / white / rgb(255,255,255), hidden attribute).
- ``<meta>`` tags and ``<title>`` as metadata.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

from bs4 import BeautifulSoup, Comment, Tag

from ingestion.hidden import (
    AuditEntry,
    HiddenContent,
    IngestionConfig,
    is_near_white,
    normalize_metadata,
    scan_zero_width,
    strip_zero_width,
)

logger = logging.getLogger(__name__)

# CSS patterns for hidden content
_DISPLAY_NONE_RE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
_VISIBILITY_HIDDEN_RE = re.compile(r"visibility\s*:\s*hidden", re.IGNORECASE)
_OPACITY_ZERO_RE = re.compile(r"opacity\s*:\s*0(?:[;\s]|$)", re.IGNORECASE)
_FONT_SIZE_ZERO_RE = re.compile(r"font-size\s*:\s*0(?:px|pt|em|rem|%)?(?:[;\s]|$)", re.IGNORECASE)

# Near-white colour patterns in CSS
_COLOR_WHITE_RE = re.compile(
    r"(?:^|;|\s)color\s*:\s*(?:white|#fff(?:fff)?|rgb\s*\(\s*255\s*,\s*255\s*,\s*255\s*\))",
    re.IGNORECASE,
)

# Tiny font-size extractor
_FONT_SIZE_RE = re.compile(
    r"font-size\s*:\s*([\d.]+)\s*(px|pt|em|rem)",
    re.IGNORECASE,
)


def _css_has_tiny_font(style: str, threshold_pt: float) -> bool:
    """Check if inline style has a font-size below the threshold."""
    m = _FONT_SIZE_RE.search(style)
    if not m:
        return False
    value = float(m.group(1))
    unit = m.group(2).lower()
    # Convert to pt (approximate)
    if unit == "px":
        value_pt = value * 0.75
    elif unit == "pt":
        value_pt = value
    elif unit in ("em", "rem"):
        value_pt = value * 12  # assume 12pt base
    else:
        value_pt = value
    return value_pt < threshold_pt and value_pt >= 0


def _element_path(tag: Tag) -> str:
    """Build a simple CSS-like path for a tag, for location reporting."""
    parts: list[str] = []
    for parent in tag.parents:
        if parent.name:
            parts.append(parent.name)
    parts.reverse()
    parts.append(tag.name)
    idx = ""
    tag_id = tag.get("id")
    if tag_id:
        idx = f"#{tag_id}"
    tag_class = tag.get("class")
    if tag_class and isinstance(tag_class, list):
        idx += "." + ".".join(tag_class)
    return " > ".join(parts) + idx


def _is_hidden_element(tag: Tag, tiny_font_pt: float) -> bool:
    """Return True if the tag has CSS or attributes indicating hidden content."""
    # hidden HTML attribute
    if tag.has_attr("hidden"):
        return True

    style = tag.get("style", "")
    if not style:
        return False

    if _DISPLAY_NONE_RE.search(style):
        return True
    if _VISIBILITY_HIDDEN_RE.search(style):
        return True
    if _OPACITY_ZERO_RE.search(style):
        return True
    if _FONT_SIZE_ZERO_RE.search(style):
        return True
    if _COLOR_WHITE_RE.search(style):
        return True
    if _css_has_tiny_font(style, tiny_font_pt):
        return True

    return False


def extract_html(
    data: bytes | str,
    config: IngestionConfig | None = None,
) -> Tuple[str, HiddenContent, List[AuditEntry]]:
    """
    Extract text and hidden content from an HTML byte-string or string.

    Parameters
    ----------
    data : bytes | str
        Raw HTML content.
    config : IngestionConfig, optional
        Tunable thresholds.

    Returns
    -------
    (raw_text, hidden_content, audit_entries)
    """
    cfg = config or IngestionConfig()
    hidden = HiddenContent()
    audit: List[AuditEntry] = []

    if isinstance(data, bytes):
        # Try UTF-8 first, then latin-1 fallback
        try:
            html_str = data.decode("utf-8")
        except UnicodeDecodeError:
            html_str = data.decode("latin-1", errors="replace")
    else:
        html_str = data

    try:
        soup = BeautifulSoup(html_str, "lxml")
    except Exception as exc:
        logger.error("Failed to parse HTML: %s", exc)
        audit.append(AuditEntry(stage="parse", status="failed", error=str(exc)))
        return "", hidden, audit

    audit.append(AuditEntry(stage="parse", status="complete"))

    # ── Metadata: <meta> tags and <title> ────────────────────────────────
    meta_raw: Dict[str, Any] = {}
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        meta_raw["title"] = title_tag.string.strip()

    for meta in soup.find_all("meta"):
        name = meta.get("name") or meta.get("property") or meta.get("http-equiv")
        content = meta.get("content", "")
        if name and content:
            meta_raw[str(name)] = content
    hidden.metadata = normalize_metadata(meta_raw)

    # ── HTML comments ────────────────────────────────────────────────────
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        hidden.html_comments.append(f"<!-- {comment.strip()} -->")

    # ── Hidden elements (CSS / attribute based) ──────────────────────────
    # We need to collect hidden element tags first so we can exclude them
    # from visible text extraction.
    hidden_tags: set[int] = set()  # ids of hidden Tag objects

    for tag in soup.find_all(True):  # all elements
        if _is_hidden_element(tag, cfg.tiny_font_pt):
            text = tag.get_text(separator=" ", strip=True)
            if text:
                path = _element_path(tag)
                style = tag.get("style", "")

                # Determine reason
                reason = "hidden"
                if tag.has_attr("hidden"):
                    reason = "hidden attribute"
                elif _DISPLAY_NONE_RE.search(style):
                    reason = "display:none"
                elif _VISIBILITY_HIDDEN_RE.search(style):
                    reason = "visibility:hidden"
                elif _OPACITY_ZERO_RE.search(style):
                    reason = "opacity:0"
                elif _FONT_SIZE_ZERO_RE.search(style):
                    reason = "font-size:0"
                elif _COLOR_WHITE_RE.search(style):
                    reason = "white text"
                elif _css_has_tiny_font(style, cfg.tiny_font_pt):
                    reason = "tiny font"

                hidden.invisible_spans.append(
                    f"[{path}] {reason}: {text}"
                )
            hidden_tags.add(id(tag))
            # Also mark all descendants
            for desc in tag.descendants:
                if isinstance(desc, Tag):
                    hidden_tags.add(id(desc))

    # ── Visible text extraction ──────────────────────────────────────────
    # Walk the tree and collect text from non-hidden elements
    visible_parts: List[str] = []

    for tag in soup.find_all(True):
        if id(tag) in hidden_tags:
            continue
        # Only collect direct text of this tag (not children's text)
        for child in tag.children:
            if isinstance(child, str) and not isinstance(child, Comment):
                text = child.strip()
                if text:
                    visible_parts.append(text)

    raw_text = " ".join(visible_parts)

    # ── Zero-width scan ──────────────────────────────────────────────────
    zw_hits = scan_zero_width(raw_text, context_chars=cfg.context_chars)
    hidden.zero_width.extend(zw_hits)
    raw_text = strip_zero_width(raw_text)

    audit.append(AuditEntry(stage="hidden_scan", status="complete"))
    return raw_text, hidden, audit
