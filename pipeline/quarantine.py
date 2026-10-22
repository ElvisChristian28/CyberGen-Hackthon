"""
pipeline/quarantine.py

Persistent quarantine store for documents blocked by the Pipeline stage.

Storage
-------
Entries are persisted in ``data/quarantine.json`` as a JSON object keyed by
``doc_id``.  All writes are atomic (write to a ``.tmp`` file then rename) and
guarded by a threading lock so concurrent web-server threads cannot corrupt the
file.

Entry shape
-----------
{
    "doc_id":         str,
    "filename":       str,
    "risk_score":     int,
    "reasons":        list[str],
    "quarantined_at": str,   # ISO-8601 UTC
    "status":         str,   # "PENDING" | "APPROVED" | "REJECTED"
    "reviewed_by":    str | None,
    "reviewed_at":    str | None,
    "doc_payload":    dict    # full DocumentPayload — needed so approve() can ingest
}

Public API
----------
    from pipeline.quarantine import add, list_pending, list_all, get, approve, reject

    add(doc)                        # quarantine a document
    items = list_pending()          # PENDING entries only
    items = list_all()              # every entry
    entry = get(doc_id)             # single entry or KeyError
    approve(doc_id, reviewer="me")  # ingest to protected DB, mark APPROVED
    reject(doc_id, reviewer="me")   # tombstone entry, mark REJECTED
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pipeline.audit as audit
from pipeline.config import cfg

logger = logging.getLogger(__name__)

_lock = threading.Lock()

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"


# ── Internal helpers ─────────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _quarantine_path() -> Path:
    cfg.ensure_data_dir()
    return cfg.QUARANTINE_FILE


def _load(path: Path) -> dict[str, Any]:
    """Load the quarantine store from disk; return an empty dict if absent."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError:
            logger.warning("Quarantine file corrupt; starting fresh.")
            return {}


def _save(store: dict[str, Any], path: Path) -> None:
    """Atomically write the store to disk."""
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


# ── Public functions ─────────────────────────────────────────────────────────

def add(
    doc: dict[str, Any],
    quarantine_file: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Add a document to the quarantine store with status PENDING.

    Parameters
    ----------
    doc:
        Full DocumentPayload dict from the Pipeline stage.
    quarantine_file:
        Override path (used by tests).

    Returns
    -------
    dict
        The newly created quarantine entry.
    """
    path = quarantine_file or _quarantine_path()
    doc_id = doc["doc_id"]

    entry: dict[str, Any] = {
        "doc_id": doc_id,
        "filename": doc.get("filename", "unknown"),
        "risk_score": doc.get("risk_score"),
        "reasons": doc.get("reasons", []),
        "quarantined_at": _utc_now(),
        "status": STATUS_PENDING,
        "reviewed_by": None,
        "reviewed_at": None,
        "doc_payload": doc,  # store full payload so approve() can ingest it
    }

    with _lock:
        store = _load(path)
        store[doc_id] = entry
        _save(store, path)

    logger.info("Quarantined doc_id=%s (%s)", doc_id, entry["filename"])
    return entry


def list_all(quarantine_file: Optional[Path] = None) -> list[dict[str, Any]]:
    """Return all quarantine entries, sorted newest first."""
    path = quarantine_file or _quarantine_path()
    store = _load(path)
    entries = list(store.values())
    entries.sort(key=lambda e: e.get("quarantined_at", ""), reverse=True)
    return entries


def list_pending(quarantine_file: Optional[Path] = None) -> list[dict[str, Any]]:
    """Return only PENDING quarantine entries."""
    return [e for e in list_all(quarantine_file) if e["status"] == STATUS_PENDING]


def get(doc_id: str, quarantine_file: Optional[Path] = None) -> dict[str, Any]:
    """
    Retrieve a single quarantine entry.

    Raises
    ------
    KeyError
        If *doc_id* is not in the quarantine store.
    """
    path = quarantine_file or _quarantine_path()
    store = _load(path)
    if doc_id not in store:
        raise KeyError(f"doc_id {doc_id!r} not found in quarantine store")
    return store[doc_id]


def approve(
    doc_id: str,
    reviewer: Optional[str] = None,
    quarantine_file: Optional[Path] = None,
    persist_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Approve a PENDING quarantine entry.

    Ingests the document's original chunks into the protected vector DB
    (a human has reviewed it and judged it safe), then updates the entry status
    to APPROVED.

    Parameters
    ----------
    doc_id:     Document to approve.
    reviewer:   Optional reviewer name/id recorded in the entry.
    quarantine_file: Override path (used by tests).
    persist_dir: Override ChromaDB directory (used by tests).

    Returns
    -------
    dict
        The updated quarantine entry.

    Raises
    ------
    KeyError
        If *doc_id* is not in the store.
    ValueError
        If the entry is not in PENDING status.
    """
    path = quarantine_file or _quarantine_path()

    with _lock:
        store = _load(path)
        if doc_id not in store:
            raise KeyError(f"doc_id {doc_id!r} not found in quarantine store")
        entry = store[doc_id]
        if entry["status"] != STATUS_PENDING:
            raise ValueError(
                f"Cannot approve doc_id {doc_id!r}: status is {entry['status']!r}, expected PENDING"
            )

        # Ingest original chunks into the protected vector DB
        from pipeline.vector_db import ingest_to_vector_db  # avoid circular import

        doc_payload = entry["doc_payload"]
        ingest_to_vector_db(doc_payload, protected=True, persist_dir=persist_dir)
        logger.info("Approved and ingested doc_id=%s into protected DB", doc_id)

        # Update entry
        entry["status"] = STATUS_APPROVED
        entry["reviewed_by"] = reviewer
        entry["reviewed_at"] = _utc_now()
        store[doc_id] = entry
        _save(store, path)

    audit.log(
        doc_id,
        entry["filename"],
        stage="quarantine",
        decision=STATUS_APPROVED,
        risk_score=entry.get("risk_score"),
        reviewer=reviewer,
    )
    return entry


def reject(
    doc_id: str,
    reviewer: Optional[str] = None,
    quarantine_file: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Reject a PENDING quarantine entry.

    Permanently discards the full document payload (``doc_payload`` is removed)
    and keeps only a tombstone entry with status REJECTED.  This ensures
    sensitive or malicious content is not retained indefinitely.

    Parameters
    ----------
    doc_id:     Document to reject.
    reviewer:   Optional reviewer name/id.
    quarantine_file: Override path (used by tests).

    Returns
    -------
    dict
        The tombstone quarantine entry (no ``doc_payload``).

    Raises
    ------
    KeyError
        If *doc_id* is not in the store.
    ValueError
        If the entry is not in PENDING status.
    """
    path = quarantine_file or _quarantine_path()

    with _lock:
        store = _load(path)
        if doc_id not in store:
            raise KeyError(f"doc_id {doc_id!r} not found in quarantine store")
        entry = store[doc_id]
        if entry["status"] != STATUS_PENDING:
            raise ValueError(
                f"Cannot reject doc_id {doc_id!r}: status is {entry['status']!r}, expected PENDING"
            )

        # Permanently discard document content
        entry.pop("doc_payload", None)
        entry["status"] = STATUS_REJECTED
        entry["reviewed_by"] = reviewer
        entry["reviewed_at"] = _utc_now()
        store[doc_id] = entry
        _save(store, path)

    logger.info("Rejected and discarded content for doc_id=%s", doc_id)
    audit.log(
        doc_id,
        entry["filename"],
        stage="quarantine",
        decision=STATUS_REJECTED,
        risk_score=entry.get("risk_score"),
        reviewer=reviewer,
    )
    return entry
