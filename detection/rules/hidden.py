"""Rules that fire on the `hidden_content` field produced by Ingestion.

Ingestion produces ``hidden_content`` as a dict with these keys:
    html_comments   : list[str]
    zero_width      : list[str]
    metadata        : dict[str, str]
    invisible_spans : list[str]

The original code iterated the dict directly (yielding keys, not values) —
fixed here to unpack each sub-list/dict correctly.  Also handles the legacy
list-of-dicts format for backwards-compatibility with hand-crafted fixtures.
"""
from .common import make_finding
from .injection import scan_injection


def _items_from_hidden_content(hidden_content):
    """
    Yield (text, kind, location_label) tuples from the hidden_content value.
    """
    if not hidden_content:
        return

    # ── Dict format: produced by ingestion.loader ─────────────────────────
    if isinstance(hidden_content, dict):
        for key in ("html_comments", "zero_width", "invisible_spans"):
            for i, text in enumerate(hidden_content.get(key) or [], 1):
                if isinstance(text, str) and text.strip():
                    yield text, key, f"{key}_{i}"
        # metadata is dict[str, str]
        for k, v in (hidden_content.get("metadata") or {}).items():
            if isinstance(v, str) and v.strip():
                yield v, f"metadata.{k}", f"metadata_{k}"
        return

    # ── Legacy list format: hand-crafted fixtures / old contracts ─────────
    if isinstance(hidden_content, list):
        for i, item in enumerate(hidden_content, 1):
            if isinstance(item, dict):
                text = item.get("text", "")
                kind = item.get("type", "hidden text")
                loc = item.get("location") or f"hidden_content_{i}"
            else:
                text, kind, loc = str(item), "hidden text", f"hidden_content_{i}"
            if text.strip():
                yield text, kind, loc


def scan_hidden(doc):
    findings = []
    for text, kind, loc in _items_from_hidden_content(doc.get("hidden_content")):
        preview = text.strip()[:80]
        findings.append(make_finding(
            "hidden_text", preview, loc, "MEDIUM",
            f"Hidden content ({kind}) found in {loc}: '{preview}'",
        ))
        # Instructions concealed in hidden content are far more suspicious
        findings.extend(scan_injection(text, loc))
    return findings
