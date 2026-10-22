"""Rules that fire on the `hidden_content` field produced by Ingestion."""
from .common import make_finding
from .injection import scan_injection


def scan_hidden(doc):
    findings = []
    for i, item in enumerate(doc.get("hidden_content") or [], 1):
        if isinstance(item, dict):
            text = item.get("text", "")
            kind = item.get("type", "hidden text")
            loc = item.get("location") or f"hidden_content_{i}"
        else:
            text, kind, loc = str(item), "hidden text", f"hidden_content_{i}"
        if not text.strip():
            continue
        preview = text.strip()[:80]
        findings.append(make_finding(
            "hidden_text", preview, loc, "MEDIUM",
            f"Hidden content ({kind}) found in {loc}: '{preview}'",
        ))
        # Instructions concealed in hidden content are far more suspicious
        findings.extend(scan_injection(text, loc))
    return findings
