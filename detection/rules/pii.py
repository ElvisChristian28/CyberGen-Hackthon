"""PII detection: regex first, Presidio as an optional fallback."""
import re
from .common import normalize, make_finding

# (label, severity, regex). Order = priority when matches overlap.
PII_PATTERNS = [
    ("credit/debit card", "HIGH",   r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"),   # Luhn-checked below
    ("Aadhaar-style number", "HIGH", r"(?<!\d)[2-9]\d{3}\s?\d{4}\s?\d{4}(?!\d)"),
    ("PAN card", "HIGH",            r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
    ("phone number (IN)", "MEDIUM", r"(?<!\d)(?:(?:\+91|91|0)[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"),
    ("email address", "MEDIUM",     r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    ("IP address", "LOW",           r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])"),
]
_COMPILED = [(l, s, re.compile(p)) for l, s, p in PII_PATTERNS]

PRESIDIO_ENTITIES = ["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE", "US_SSN"]
_analyzer = None  # None = not loaded yet, False = unavailable


def luhn_valid(number):
    digits = [int(c) for c in re.sub(r"\D", "", number)]
    if not 13 <= len(digits) <= 19:
        return False
    total, alt = 0, False
    for d in reversed(digits):
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _presidio(text):
    global _analyzer
    if _analyzer is False:
        return []
    try:
        if _analyzer is None:
            from presidio_analyzer import AnalyzerEngine
            _analyzer = AnalyzerEngine()
        return _analyzer.analyze(text=text, language="en",
                                 entities=PRESIDIO_ENTITIES, score_threshold=0.6)
    except Exception:
        _analyzer = False
        return []


def scan_pii(text, location, use_presidio=True):
    text = normalize(text)
    findings, claimed = [], []   # claimed = (start, end) spans already matched

    def overlaps(s, e):
        return any(s < ce and e > cs for cs, ce in claimed)

    for label, severity, rx in _COMPILED:
        for m in rx.finditer(text):
            s, e = m.span()
            if overlaps(s, e):
                continue
            if label == "credit/debit card" and not luhn_valid(m.group(0)):
                continue
            claimed.append((s, e))
            findings.append(make_finding(
                "pii", m.group(0), location, severity,
                f"PII detected: {label} in {location}", pii_type=label,
            ))

    if use_presidio:
        known = {f["matched"] for f in findings}
        for r in _presidio(text):
            value = text[r.start:r.end]
            if value in known or overlaps(r.start, r.end):
                continue
            claimed.append((r.start, r.end))
            findings.append(make_finding(
                "pii", value, location, "MEDIUM",
                f"PII detected: {r.entity_type.lower().replace('_', ' ')} in {location} (Presidio)",
                pii_type=r.entity_type,
            ))
    return findings
