"""
shared/adapters.py
==================
Thin translation layer between the Ingestion stage output and the Detection
stage input.

Why this exists
---------------
Ingestion and Detection were developed independently.  Rather than editing
either teammate's internals, mismatches are resolved here so each stage's
code stays untouched (except for the ``scan_hidden`` bug fix, which is
documented in the final report).

Current translations
--------------------
1. **chunks** — Ingestion already produces ``[{"index": int, "text": str}]``
   and Detection's ``get_chunks()`` handles both forms, so NO translation is
   needed for chunks.  The normaliser is called defensively to guarantee the
   canonical form even if a future ingestion change regresses.

2. **hidden_content** — NO translation needed after the ``scan_hidden`` bug
   fix; detection now correctly reads the dict form that ingestion produces.

3. **doc_id** — always present (uuid4 set in ``IngestionResult.__post_init__``).

The adapter is intentionally a thin pass-through so that when the stages
converge on the shared schema it can be deleted without any other changes.
"""
from __future__ import annotations

import copy
import logging
from typing import Any

from shared.schema import normalize_chunks

logger = logging.getLogger(__name__)


def ingestion_to_detection(doc: dict[str, Any]) -> dict[str, Any]:
    """
    Prepare an ingestion output dict for the detection stage.

    Parameters
    ----------
    doc:
        Dict returned by ``ingestion.loader.ingest()``.  This function does
        NOT mutate the original.

    Returns
    -------
    dict
        A shallow copy of *doc* with:
        - ``chunks`` normalised to ``[{"index": int, "text": str}]``.
        - All other fields passed through unchanged.
    """
    out = copy.copy(doc)  # shallow copy — we only replace top-level keys

    # ── 1. Normalise chunks (defensive; ingestion already uses object form) ──
    raw_chunks = doc.get("chunks", [])
    normalised = normalize_chunks(raw_chunks)
    if normalised != raw_chunks:
        logger.debug(
            "adapter: normalised %d chunks from plain-string form",
            len(normalised),
        )
    out["chunks"] = normalised

    # ── 2. hidden_content: dict form is already correct after bug fix ──────
    # No translation needed.

    # ── 3. Ensure detection pre-conditions are present ────────────────────
    out.setdefault("findings", [])
    out.setdefault("risk_score", 0)
    out.setdefault("reasons", [])
    out.setdefault("decision", None)
    out.setdefault("sanitized_text", None)

    return out
