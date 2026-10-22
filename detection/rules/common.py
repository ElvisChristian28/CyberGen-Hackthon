"""Shared helpers for the rule modules."""
import unicodedata

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


def normalize(text):
    """Defeat simple evasion: fullwidth/lookalike chars and zero-width characters."""
    return unicodedata.normalize("NFKC", text or "").translate(_ZERO_WIDTH)


def make_finding(ftype, matched, location, severity, reason, **extra):
    f = {
        "type": ftype,
        "matched": matched,
        "location": location,
        "severity": severity,
        "reason": reason,
    }
    f.update(extra)
    return f
