"""Aggregate findings into a 0-100 risk score plus plain-language reasons."""
from collections import defaultdict

SEVERITY_WEIGHTS = {
    "prompt_injection": 40,
    "role_override": 35,
    "data_exfiltration": 30,
    "pii": 15,
    "hidden_text": 20,
    "llm_triage": 30,
}
CONCEALMENT_BONUS = 10        # hidden_text AND prompt_injection both fire
MAX_COUNTED_PER_TYPE = 3      # stops one PII-heavy doc from hitting 100 on its own


def compute(findings):
    counted = defaultdict(int)
    total = 0
    for f in findings:
        ftype = f["type"]
        if counted[ftype] >= MAX_COUNTED_PER_TYPE:
            continue
        counted[ftype] += 1
        total += f.get("weight", SEVERITY_WEIGHTS.get(ftype, 0))

    types = {f["type"] for f in findings}
    reasons = [f["reason"] for f in findings]
    if "hidden_text" in types and "prompt_injection" in types:
        total += CONCEALMENT_BONUS
        reasons.append("Concealment: instructions were hidden inside the document (+10)")
    return min(100, total), reasons
