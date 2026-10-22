"""
shared/schema.py
================
Canonical TypedDicts and runtime validators for the CyberGen inter-stage
document contract.

Every stage reads and writes the same DocumentPayload dict so that modules
can be developed independently.

Public API
----------
    from shared.schema import (
        validate_ingested,   # raises ValueError if ingestion output is wrong
        validate_scored,     # raises ValueError if detection output is wrong
        normalize_chunks,    # coerces plain strings or dicts to {index, text}
        Finding, Chunk, AuditEntry, DocumentPayload,
    )
"""
from __future__ import annotations

from typing import Any, List, Optional, Union
from typing_extensions import TypedDict, NotRequired


# ── TypedDict definitions ────────────────────────────────────────────────────

class Finding(TypedDict):
    """A single threat or PII detection produced by the Detection stage."""
    type: str        # "prompt_injection" | "pii" | "hidden_text" | "role_override" | ...
    matched: str     # the exact substring that triggered the rule
    location: str    # e.g. "chunk_1" or "metadata_Author"
    severity: str    # "HIGH" | "MEDIUM" | "LOW"
    reason: NotRequired[str]    # plain-language explanation (always present in practice)
    weight: NotRequired[int]    # scorer override


class Chunk(TypedDict):
    """A text chunk produced by the Ingestion stage."""
    index: int
    text: str


class AuditEntry(TypedDict):
    """One record in the document's embedded audit trail list."""
    stage: str
    status: NotRequired[str]
    ts: str
    decision: NotRequired[str]
    findings_count: NotRequired[int]
    error: NotRequired[str]


class DocumentPayload(TypedDict):
    """
    The canonical document object passed between all four pipeline stages.

    Fields added by each stage:
      Ingestion  -> doc_id, filename, raw_text, chunks, hidden_content,
                    timestamp
      Detection  -> findings, risk_score, reasons
      Pipeline   -> sanitized_text, decision, in_vector_db, in_quarantine
    """
    # ── Ingestion fields ─────────────────────────────────────────────────
    doc_id: str
    filename: str
    raw_text: str
    chunks: List[Chunk]
    hidden_content: NotRequired[dict]
    timestamp: NotRequired[str]

    # ── Detection fields ─────────────────────────────────────────────────
    findings: List[Finding]
    risk_score: int
    reasons: List[str]

    # ── Pipeline fields (set by pipeline stage) ───────────────────────────
    sanitized_text: NotRequired[Optional[str]]
    decision: NotRequired[Optional[str]]
    in_vector_db: NotRequired[bool]
    in_quarantine: NotRequired[bool]

    # ── Shared ───────────────────────────────────────────────────────────
    audit_trail: List[AuditEntry]


# ── normalize_chunks ─────────────────────────────────────────────────────────

def normalize_chunks(
    chunks: List[Union[str, dict]],
) -> List[Chunk]:
    """
    Accept either a list of plain strings or a list of ``{index, text}`` dicts
    and always return the dict form.

    Parameters
    ----------
    chunks:
        Raw chunk list from any stage.

    Returns
    -------
    List[Chunk]
        Canonical ``[{"index": int, "text": str}, ...]`` form.

    Examples
    --------
    >>> normalize_chunks(["hello", "world"])
    [{'index': 0, 'text': 'hello'}, {'index': 1, 'text': 'world'}]
    >>> normalize_chunks([{"index": 0, "text": "hello"}])
    [{'index': 0, 'text': 'hello'}]
    """
    result: List[Chunk] = []
    for i, c in enumerate(chunks or []):
        if isinstance(c, dict):
            result.append({"index": int(c.get("index", i)), "text": str(c.get("text", ""))})
        else:
            result.append({"index": i, "text": str(c)})
    return result


# ── Validators ───────────────────────────────────────────────────────────────

# Fields required immediately after ingestion
_INGESTED_REQUIRED: dict[str, type | tuple] = {
    "doc_id":      str,
    "filename":    str,
    "raw_text":    str,
    "chunks":      list,
    "audit_trail": list,
}

# Additional fields required after detection
_SCORED_REQUIRED: dict[str, type | tuple] = {
    "findings":    list,
    "risk_score":  (int, float),
    "reasons":     list,
}


def _check_fields(
    doc: Any,
    required: dict[str, type | tuple],
    stage: str,
) -> None:
    """Raise ValueError naming the first broken field and the responsible stage."""
    if not isinstance(doc, dict):
        raise ValueError(
            f"[{stage}] document must be a dict, got {type(doc).__name__!r}"
        )
    for field, expected in required.items():
        if field not in doc:
            raise ValueError(
                f"[{stage}] missing required field {field!r}"
            )
        if not isinstance(doc[field], expected):
            got = type(doc[field]).__name__
            exp = (
                " | ".join(t.__name__ for t in expected)
                if isinstance(expected, tuple)
                else expected.__name__
            )
            raise ValueError(
                f"[{stage}] field {field!r} must be {exp}, got {got!r}"
            )

    # Chunks must be dicts with 'index' and 'text' if present
    if "chunks" in required and "chunks" in doc:
        for idx, chunk in enumerate(doc["chunks"]):
            if not isinstance(chunk, dict):
                raise ValueError(
                    f"[{stage}] chunks[{idx}] must be a dict, got "
                    f"{type(chunk).__name__!r}"
                )
            for key in ("index", "text"):
                if key not in chunk:
                    raise ValueError(
                        f"[{stage}] chunks[{idx}] missing key {key!r}"
                    )


def validate_ingested(doc: Any) -> None:
    """
    Validate that *doc* satisfies the post-ingestion contract.

    Raises
    ------
    ValueError
        With a message that names the missing/wrong field and states
        that ingestion is responsible.
    """
    _check_fields(doc, _INGESTED_REQUIRED, stage="ingestion")

    # audit_trail must contain at least one ingestion entry
    trail = doc.get("audit_trail", [])
    if not any(
        isinstance(e, dict) and e.get("stage") == "ingestion"
        for e in trail
    ):
        raise ValueError(
            "[ingestion] audit_trail must contain at least one entry "
            "with stage='ingestion'"
        )


def validate_scored(doc: Any) -> None:
    """
    Validate that *doc* satisfies the post-detection contract.

    Raises
    ------
    ValueError
        With a message that names the missing/wrong field and states
        that detection is responsible.
    """
    # Must also satisfy the ingestion contract
    _check_fields(doc, _INGESTED_REQUIRED, stage="ingestion")
    # Plus detection-added fields
    _check_fields(doc, _SCORED_REQUIRED, stage="detection")

    risk = doc.get("risk_score")
    if not (0 <= risk <= 100):
        raise ValueError(
            f"[detection] risk_score must be in [0, 100], got {risk!r}"
        )

    # audit_trail must contain a detection entry
    trail = doc.get("audit_trail", [])
    if not any(
        isinstance(e, dict) and e.get("stage") == "detection"
        for e in trail
    ):
        raise ValueError(
            "[detection] audit_trail must contain at least one entry "
            "with stage='detection'"
        )

    # Every finding must have 'type' and 'matched'
    for i, f in enumerate(doc.get("findings", [])):
        if not isinstance(f, dict):
            raise ValueError(
                f"[detection] findings[{i}] must be a dict, got "
                f"{type(f).__name__!r}"
            )
        for key in ("type", "matched"):
            if key not in f:
                raise ValueError(
                    f"[detection] findings[{i}] missing key {key!r}"
                )
