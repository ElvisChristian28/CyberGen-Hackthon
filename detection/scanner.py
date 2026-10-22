"""Stage 2 entry point.

    python -m detection.scanner path/to/mock_ingestion_output.json
"""
import copy
import json
import sys
from datetime import datetime, timezone

from .rules.common import normalize
from .rules.injection import scan_injection
from .rules.pii import scan_pii
from .rules.hidden import scan_hidden
from .scorer import compute
from .llm_triage import triage

TRIAGE_MIN, TRIAGE_MAX = 40, 70     # LLM only when 40 <= score < 70


def get_chunks(doc):
    """Return [(chunk_id, text)]. Adjust here if the shared schema differs."""
    chunks = doc.get("chunks")
    if chunks:
        out = []
        for i, c in enumerate(chunks, 1):
            if isinstance(c, dict):
                out.append((c.get("id") or c.get("chunk_id") or f"chunk_{i}", c.get("text", "")))
            else:
                out.append((f"chunk_{i}", str(c)))
        return out
    return [("chunk_1", doc.get("text") or doc.get("raw_text") or "")]


def scan(doc, llm_call=None, use_presidio=True):
    doc = copy.deepcopy(doc)
    chunks = get_chunks(doc)
    findings = []
    for loc, text in chunks:
        findings += scan_injection(text, loc)
        findings += scan_pii(text, loc, use_presidio=use_presidio)
    findings += scan_hidden(doc)

    score, reasons = compute(findings)

    if TRIAGE_MIN <= score < TRIAGE_MAX:
        flagged = {f["location"] for f in findings}
        suspicious = [(l, t) for l, t in chunks if l in flagged] or chunks
        findings.append(triage(suspicious, llm_call))
        score, reasons = compute(findings)

    doc["findings"] = findings
    doc["risk_score"] = score
    doc["reasons"] = reasons
    doc.setdefault("decision", None)          # Jordan's stage sets these
    doc.setdefault("sanitized_text", None)
    doc.setdefault("audit_trail", []).append({
        "stage": "detection",
        "status": "complete",
        "findings_count": len(findings),
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    return doc


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m detection.scanner <ingestion_output.json>")
    with open(sys.argv[1], encoding="utf-8") as fh:
        print(json.dumps(scan(json.load(fh)), indent=2, ensure_ascii=False))
