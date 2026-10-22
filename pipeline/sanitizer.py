"""
pipeline/sanitizer.py

Redacts threat findings from document text and chunks.

Design decisions
----------------
- Longer matched strings are replaced first so a shorter match that is a
  substring of a longer one does not destroy the longer replacement marker.
- Matches not found in the text are skipped silently (idempotent).
- Both full-text and chunk-level sanitization are provided because the vector
  database ingests *chunks*, not the raw full text.  If only the full text were
  redacted, poisoned content would still reach the embedding store.

Redaction format
----------------
    [REDACTED: prompt_injection]
    [REDACTED: PII-email]
    [REDACTED: hidden_text]

Usage
-----
    from pipeline.sanitizer import sanitize, sanitize_chunks

    clean = sanitize(raw_text, findings)
    clean_chunks = sanitize_chunks(chunks, findings)
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Map finding types to human-readable redaction labels.
_TYPE_LABELS: dict[str, str] = {
    "prompt_injection": "prompt_injection",
    "role_override": "role_override",
    "data_exfiltration": "data_exfiltration",
    "hidden_text": "hidden_text",
    "pii": "PII",
    # PII sub-types from Detection / Presidio
    "pii_email": "PII-email",
    "pii_phone": "PII-phone",
    "pii_aadhaar": "PII-aadhaar",
    "pii_credit_card": "PII-credit_card",
    "pii_pan": "PII-pan",
    "pii_ip": "PII-ip",
    "llm_triage": "llm_triage",
}


def _label_for(finding_type: str) -> str:
    """Return the redaction label for a finding type."""
    return _TYPE_LABELS.get(finding_type, finding_type)


def sanitize(text: str, findings: list[dict[str, Any]]) -> str:
    """
    Replace each finding's matched text with a ``[REDACTED: <type>]`` marker.

    Parameters
    ----------
    text:
        The full document text to sanitize.
    findings:
        List of Finding dicts from the Detection stage.  Each must have at
        least ``"matched"`` (str) and ``"type"`` (str) keys.

    Returns
    -------
    str
        A copy of *text* with all matched substrings replaced.
        Returns *text* unchanged if *findings* is empty.
    """
    if not findings:
        return text

    # Sort by descending match length so longer matches are replaced first.
    sorted_findings = sorted(
        findings,
        key=lambda f: len(f.get("matched", "")),
        reverse=True,
    )

    result = text
    for finding in sorted_findings:
        matched = finding.get("matched", "")
        ftype = finding.get("type", "unknown")

        if not matched:
            logger.debug("Skipping finding with empty 'matched' field (type=%s)", ftype)
            continue

        if matched not in result:
            logger.debug(
                "Matched text not found in document text (type=%s, matched=%r) — skipping",
                ftype,
                matched[:80],
            )
            continue

        label = f"[REDACTED: {_label_for(ftype)}]"
        count = result.count(matched)
        result = result.replace(matched, label)
        logger.debug("Redacted %d occurrence(s) of type=%s", count, ftype)

    return result


def sanitize_chunks(
    chunks: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Apply sanitization to every chunk in the document.

    Each chunk dict must have at least ``"index"`` (int) and ``"text"`` (str).
    Returns a new list of chunk dicts with sanitized ``"text"`` values; all
    other fields are preserved unchanged.

    Parameters
    ----------
    chunks:
        List of Chunk dicts, e.g. ``[{"index": 0, "text": "..."}, ...]``.
    findings:
        Same findings list passed to :func:`sanitize`.
    """
    if not findings:
        return [dict(c) for c in chunks]  # shallow copy, nothing to redact

    sanitized: list[dict[str, Any]] = []
    for chunk in chunks:
        new_chunk = dict(chunk)
        new_chunk["text"] = sanitize(chunk.get("text", ""), findings)
        sanitized.append(new_chunk)

    return sanitized
