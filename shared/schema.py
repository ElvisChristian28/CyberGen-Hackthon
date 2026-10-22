"""
shared/schema.py
Minimal TypedDict definitions for the CyberGen inter-stage contract.

Every stage reads and writes the same DocumentPayload dict so that modules
can be developed independently against mock data.
"""
from __future__ import annotations

from typing import Any, Optional
from typing_extensions import TypedDict, NotRequired


class Finding(TypedDict):
    """A single threat or PII detection produced by the Detection stage."""
    type: str          # e.g. "prompt_injection", "pii", "hidden_text", "role_override"
    matched: str       # the exact substring that triggered the rule
    location: str      # e.g. "chunk_3" or "metadata"
    severity: str      # "HIGH" | "MEDIUM" | "LOW"


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
      Ingestion  -> doc_id, filename, raw_text, chunks, hidden_content
      Detection  -> findings, risk_score, reasons
      Pipeline   -> sanitized_text, decision, in_vector_db, in_quarantine
    """
    # ── Ingestion fields ─────────────────────────────────────────────────
    doc_id: str
    filename: str
    raw_text: str
    chunks: list[Chunk]
    hidden_content: NotRequired[dict[str, Any]]

    # ── Detection fields ─────────────────────────────────────────────────
    findings: list[Finding]
    risk_score: int
    reasons: list[str]

    # ── Pipeline fields (set by this stage) ──────────────────────────────
    sanitized_text: NotRequired[Optional[str]]
    decision: NotRequired[Optional[str]]
    in_vector_db: NotRequired[bool]
    in_quarantine: NotRequired[bool]

    # ── Shared ───────────────────────────────────────────────────────────
    audit_trail: list[AuditEntry]
