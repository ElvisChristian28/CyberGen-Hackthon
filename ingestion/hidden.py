"""
hidden.py – Shared utilities for hidden-content detection.

Provides:
- Zero-width character scanner
- Color / near-white helpers
- Metadata normalizer
- Output schema (dataclass) and validator
- IngestionConfig for tunable thresholds
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# ── Zero-width characters to detect ──────────────────────────────────────────
ZERO_WIDTH_CHARS: frozenset[str] = frozenset(
    "\u200b"  # zero-width space
    "\u200c"  # zero-width non-joiner
    "\u200d"  # zero-width joiner
    "\ufeff"  # byte-order mark / zero-width no-break space
    "\u2060"  # word joiner
)

ZERO_WIDTH_PATTERN: re.Pattern[str] = re.compile(
    r"[\u200b\u200c\u200d\ufeff\u2060]"
)

# ── Colour helpers ───────────────────────────────────────────────────────────
# Default: each channel > 0xF0 is considered "near-white"
DEFAULT_NEAR_WHITE_THRESHOLD: int = 0xF0

# ── Font-size threshold (pt) ────────────────────────────────────────────────
DEFAULT_TINY_FONT_PT: float = 2.0

# ── File-size limit (bytes, 25 MB) ──────────────────────────────────────────
DEFAULT_MAX_FILE_SIZE: int = 25 * 1024 * 1024

# ── Chunk defaults ──────────────────────────────────────────────────────────
DEFAULT_CHUNK_TARGET_TOKENS: int = 512
DEFAULT_CHUNK_OVERLAP_TOKENS: int = 50


# ─── Configuration ──────────────────────────────────────────────────────────
@dataclass
class IngestionConfig:
    """Tunable knobs for the ingestion pipeline."""

    max_file_size: int = DEFAULT_MAX_FILE_SIZE
    near_white_threshold: int = DEFAULT_NEAR_WHITE_THRESHOLD
    tiny_font_pt: float = DEFAULT_TINY_FONT_PT
    chunk_target_tokens: int = DEFAULT_CHUNK_TARGET_TOKENS
    chunk_overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS
    context_chars: int = 40  # chars of context around zero-width hits


# ─── Output schema ──────────────────────────────────────────────────────────
@dataclass
class HiddenContent:
    """Container for all hidden-content findings."""

    html_comments: List[str] = field(default_factory=list)
    zero_width: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    invisible_spans: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "html_comments": list(self.html_comments),
            "zero_width": list(self.zero_width),
            "metadata": dict(self.metadata),
            "invisible_spans": list(self.invisible_spans),
        }


@dataclass
class AuditEntry:
    """One step in the audit trail."""

    stage: str
    status: str
    ts: str = ""
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = _utcnow_iso()

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"stage": self.stage, "status": self.status, "ts": self.ts}
        if self.error is not None:
            d["error"] = self.error
        return d


@dataclass
class Chunk:
    """One text chunk."""

    index: int
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "text": self.text}


@dataclass
class IngestionResult:
    """The full output contract."""

    doc_id: str = ""
    filename: str = ""
    raw_text: str = ""
    hidden_content: HiddenContent = field(default_factory=HiddenContent)
    chunks: List[Chunk] = field(default_factory=list)
    findings: List[Any] = field(default_factory=list)
    risk_score: int = 0
    decision: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    sanitized_text: Optional[str] = None
    timestamp: str = ""
    audit_trail: List[AuditEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.doc_id:
            self.doc_id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = _utcnow_iso()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "filename": self.filename,
            "raw_text": self.raw_text,
            "hidden_content": self.hidden_content.to_dict(),
            "chunks": [c.to_dict() for c in self.chunks],
            "findings": list(self.findings),
            "risk_score": self.risk_score,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "sanitized_text": self.sanitized_text,
            "timestamp": self.timestamp,
            "audit_trail": [a.to_dict() for a in self.audit_trail],
        }


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    """Return current UTC time in ISO-8601 format with 'Z' suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_near_white(r: int, g: int, b: int, threshold: int = DEFAULT_NEAR_WHITE_THRESHOLD) -> bool:
    """Return True if all RGB channels exceed *threshold* (near-white)."""
    return r > threshold and g > threshold and b > threshold


def rgb_from_hex(hex_color: int) -> tuple[int, int, int]:
    """Convert a 24-bit int (0xRRGGBB) to (r, g, b) tuple."""
    return ((hex_color >> 16) & 0xFF, (hex_color >> 8) & 0xFF, hex_color & 0xFF)


def is_tiny_font(size_pt: float, threshold: float = DEFAULT_TINY_FONT_PT) -> bool:
    """Return True if *size_pt* is below the tiny-font threshold."""
    return size_pt < threshold


# ─── Zero-width scanner ─────────────────────────────────────────────────────

def scan_zero_width(
    text: str,
    *,
    location_prefix: str = "",
    context_chars: int = 40,
) -> list[str]:
    """
    Scan *text* for zero-width characters.

    Returns a list of strings, each containing the surrounding context with
    the zero-width character represented by its Unicode name, prefixed with
    *location_prefix* if given.

    Example return value::

        ["[page 1] ...normal text ‹ZERO WIDTH SPACE› more text..."]
    """
    hits: list[str] = []
    for m in ZERO_WIDTH_PATTERN.finditer(text):
        start = max(0, m.start() - context_chars)
        end = min(len(text), m.end() + context_chars)
        before = text[start : m.start()].replace("\n", "\\n")
        after = text[m.end() : end].replace("\n", "\\n")
        char_name = _zwc_name(m.group())
        entry = f"{before} <{char_name}> {after}".strip()
        if location_prefix:
            entry = f"{location_prefix} {entry}"
        hits.append(entry)
    return hits


def strip_zero_width(text: str) -> str:
    """Remove all zero-width characters from *text*."""
    return ZERO_WIDTH_PATTERN.sub("", text)


def _zwc_name(ch: str) -> str:
    """Human-readable name for a zero-width char."""
    names = {
        "\u200b": "ZERO WIDTH SPACE",
        "\u200c": "ZERO WIDTH NON-JOINER",
        "\u200d": "ZERO WIDTH JOINER",
        "\ufeff": "BOM/ZERO WIDTH NO-BREAK SPACE",
        "\u2060": "WORD JOINER",
    }
    return names.get(ch, f"U+{ord(ch):04X}")


# ─── Metadata normalizer ────────────────────────────────────────────────────

def normalize_metadata(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Drop None/empty values and convert non-str values to str.
    Returns a clean metadata dict.
    """
    cleaned: Dict[str, Any] = {}
    for k, v in raw.items():
        if v is None:
            continue
        s = str(v).strip() if not isinstance(v, str) else v.strip()
        if s:
            cleaned[k] = s
    return cleaned


# ─── Output validator ───────────────────────────────────────────────────────

_TOP_FIELDS: dict[str, type | tuple[type, ...]] = {
    "doc_id": str,
    "filename": str,
    "raw_text": str,
    "hidden_content": dict,
    "chunks": list,
    "findings": list,
    "risk_score": (int, float),
    "decision": (str, type(None)),
    "reasons": list,
    "sanitized_text": (str, type(None)),
    "timestamp": str,
    "audit_trail": list,
}

_HIDDEN_FIELDS: dict[str, type] = {
    "html_comments": list,
    "zero_width": list,
    "metadata": dict,
    "invisible_spans": list,
}


def validate_output(obj: Any) -> list[str]:
    """
    Validate *obj* (a dict) against the output contract.

    Returns a list of error strings.  An empty list means valid.
    """
    errors: list[str] = []
    if not isinstance(obj, dict):
        return [f"Expected dict, got {type(obj).__name__}"]

    for fname, ftype in _TOP_FIELDS.items():
        if fname not in obj:
            errors.append(f"Missing field: {fname}")
        elif not isinstance(obj[fname], ftype):
            errors.append(
                f"Field '{fname}' expected {ftype}, got {type(obj[fname]).__name__}"
            )

    hc = obj.get("hidden_content")
    if isinstance(hc, dict):
        for fname, ftype in _HIDDEN_FIELDS.items():
            if fname not in hc:
                errors.append(f"hidden_content missing field: {fname}")
            elif not isinstance(hc[fname], ftype):
                errors.append(
                    f"hidden_content.{fname} expected {ftype.__name__}, "
                    f"got {type(hc[fname]).__name__}"
                )

    # Chunks must have index & text
    for i, chunk in enumerate(obj.get("chunks", [])):
        if not isinstance(chunk, dict):
            errors.append(f"chunks[{i}] is not a dict")
        else:
            if "index" not in chunk:
                errors.append(f"chunks[{i}] missing 'index'")
            if "text" not in chunk:
                errors.append(f"chunks[{i}] missing 'text'")

    # Audit trail entries
    for i, entry in enumerate(obj.get("audit_trail", [])):
        if not isinstance(entry, dict):
            errors.append(f"audit_trail[{i}] is not a dict")
        else:
            for k in ("stage", "status", "ts"):
                if k not in entry:
                    errors.append(f"audit_trail[{i}] missing '{k}'")

    return errors
