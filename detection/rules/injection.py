"""Prompt-injection, role-override and data-exfiltration rules."""
import re
from .common import normalize, make_finding

# (finding type, regex). Types map to SEVERITY_WEIGHTS in scorer.py
PATTERNS = [
    # --- prompt injection ---
    ("prompt_injection", r"ignore\s+(all\s+)?(the\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)"),
    ("prompt_injection", r"disregard\s+(your\s+|all\s+)?(prior|previous|above|earlier)\s+\w+"),
    ("prompt_injection", r"bypass\s+(your\s+)?(safety|filters?|restrictions?)"),
    ("prompt_injection", r"do\s+anything\s+now"),
    ("prompt_injection", r"<\s*/?\s*system\s*>|\[/?INST\]|<\|im_start\|>"),   # fake delimiters
    # --- role override ---
    ("role_override", r"you\s+are\s+now\s+an?\s+\w+"),
    ("role_override", r"act\s+as\s+(if\s+you\s+are\s+|an?\s+)\w+"),
    ("role_override", r"(pretend|behave)\s+(to\s+be|like|that\s+you\s+are)"),
    # --- data exfiltration ---
    ("data_exfiltration", r"reveal\s+(your\s+)?(system\s+)?(prompt|instructions)"),
    ("data_exfiltration", r"print\s+(the\s+)?contents?\s+of\s+"),
    ("data_exfiltration", r"exfiltrate|leak\s+(all\s+)?(the\s+)?data"),
    ("data_exfiltration", r"(send|forward|upload|transmit)\b[^.\n]{0,60}\bto\s+https?://\S+"),
]

_COMPILED = [(t, re.compile(p, re.IGNORECASE)) for t, p in PATTERNS]


def scan_injection(text, location):
    text = normalize(text)
    findings, seen = [], set()
    for ftype, rx in _COMPILED:
        for m in rx.finditer(text):
            key = (ftype, m.group(0).lower())
            if key in seen:
                continue
            seen.add(key)
            matched = m.group(0).strip()
            findings.append(make_finding(
                ftype, matched, location, "HIGH",
                f"Found {ftype.replace('_', ' ')} phrase '{matched}' in {location}",
            ))
    return findings
