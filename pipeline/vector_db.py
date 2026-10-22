"""
pipeline/vector_db.py

ChromaDB wrapper for the CyberGen pipeline.

Two collections
---------------
- ``cybergen_docs``   (protected)   — receives only ALLOWED or SANITIZED chunks.
- ``unprotected_docs``              — receives ALL documents' raw chunks,
                                      regardless of decision.  This feeds the
                                      demo contrast that shows how an unprotected
                                      RAG system can be exploited.

Design decisions
----------------
- The embedding model is loaded lazily and cached in a module-level variable so
  it is only downloaded / initialised once per process.
- ``upsert`` is used so re-running the pipeline on the same document is
  idempotent.
- Chunk IDs follow the pattern ``{doc_id}_chunk_{index}`` so they are stable
  and unique within a collection.

Usage
-----
    from pipeline.vector_db import ingest_to_vector_db, ingest_unprotected, query, remove_doc

    ingest_to_vector_db(doc, protected=True)
    ingest_unprotected(doc)
    results = query("What does the report say?", protected=True)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.config import Settings

from pipeline.config import cfg

logger = logging.getLogger(__name__)

# ── Module-level singletons ──────────────────────────────────────────────────
_client: Optional[chromadb.PersistentClient] = None
_embedder = None  # sentence_transformers.SentenceTransformer, loaded lazily


def _get_client(persist_dir: Optional[Path] = None) -> chromadb.PersistentClient:
    """Return (or create) the shared ChromaDB persistent client."""
    global _client
    if _client is None:
        directory = str(persist_dir or cfg.CHROMA_PERSIST_DIR)
        cfg.ensure_data_dir()
        _client = chromadb.PersistentClient(
            path=directory,
            settings=Settings(anonymized_telemetry=False),
        )
        logger.info("ChromaDB client initialised at %s", directory)
    return _client


def _get_embedder():
    """Return (or create) the shared SentenceTransformer model."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer  # lazy import

        logger.info("Loading embedding model: %s", cfg.EMBEDDING_MODEL)
        _embedder = SentenceTransformer(cfg.EMBEDDING_MODEL)
    return _embedder


def _get_collection(
    name: str,
    persist_dir: Optional[Path] = None,
) -> chromadb.Collection:
    """Return a ChromaDB collection, creating it if it doesn't exist."""
    client = _get_client(persist_dir)
    return client.get_or_create_collection(name)


def reset_client() -> None:
    """
    Discard the cached client and embedder (used by tests and the reset script
    so that a fresh persist directory takes effect without restarting Python).
    """
    global _client, _embedder
    _client = None
    _embedder = None


# ── Public API ───────────────────────────────────────────────────────────────

def ingest_to_vector_db(
    doc: dict[str, Any],
    protected: bool = True,
    persist_dir: Optional[Path] = None,
) -> None:
    """
    Embed a document's chunks and upsert them into the chosen collection.

    For SANITIZE documents, ``doc`` should already have ``chunks`` replaced
    with sanitized chunks *before* calling this function (main.py is
    responsible for that).  The raw_text field is not used here — chunks are
    the unit of embedding.

    Parameters
    ----------
    doc:
        Full document payload (must have ``doc_id``, ``filename``, ``chunks``).
    protected:
        If True, upsert into ``cybergen_docs``; otherwise ``unprotected_docs``.
    persist_dir:
        Override the ChromaDB directory (used by tests).
    """
    collection_name = (
        cfg.PROTECTED_COLLECTION if protected else cfg.UNPROTECTED_COLLECTION
    )
    collection = _get_collection(collection_name, persist_dir)

    doc_id = doc["doc_id"]
    filename = doc["filename"]
    chunks: list[dict[str, Any]] = doc.get("chunks", [])

    if not chunks:
        logger.warning("No chunks to ingest for doc_id=%s (%s)", doc_id, filename)
        return

    embedder = _get_embedder()
    texts = [c["text"] for c in chunks]
    ids = [f"{doc_id}_chunk_{c['index']}" for c in chunks]
    metadatas = [{"doc_id": doc_id, "filename": filename} for _ in chunks]

    logger.info(
        "Ingesting %d chunks into collection=%s for doc_id=%s",
        len(chunks),
        collection_name,
        doc_id,
    )

    embeddings = embedder.encode(texts, show_progress_bar=False).tolist()
    collection.upsert(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    logger.info("Upsert complete for doc_id=%s → %s", doc_id, collection_name)


def ingest_unprotected(
    doc: dict[str, Any],
    persist_dir: Optional[Path] = None,
) -> None:
    """
    Always store the original raw chunks in the unprotected collection.

    This is called for every document regardless of decision so the demo can
    contrast a naive RAG with CyberGen's protected one.

    If ``doc`` has been modified (e.g. chunks replaced with sanitized text),
    pass the original chunks explicitly — see main.py which keeps a copy.
    """
    # We need the raw chunks.  main.py stores them under "raw_chunks" before
    # any sanitization, so prefer that key when present.
    raw_doc = dict(doc)
    if "raw_chunks" in doc:
        raw_doc["chunks"] = doc["raw_chunks"]

    ingest_to_vector_db(raw_doc, protected=False, persist_dir=persist_dir)


def query(
    question: str,
    protected: bool = True,
    top_k: Optional[int] = None,
    persist_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Query one of the two collections and return the top-k most relevant chunks.

    Parameters
    ----------
    question:   Natural-language query string.
    protected:  Which collection to search.
    top_k:      Number of results (defaults to ``cfg.RAG_TOP_K``).
    persist_dir: Override persist directory (used by tests).

    Returns
    -------
    dict with keys:
        ``documents``   — list of chunk texts (strings)
        ``metadatas``   — list of metadata dicts
        ``distances``   — list of distance floats (lower = closer)
    """
    k = top_k or cfg.RAG_TOP_K
    collection_name = (
        cfg.PROTECTED_COLLECTION if protected else cfg.UNPROTECTED_COLLECTION
    )
    collection = _get_collection(collection_name, persist_dir)

    count = collection.count()
    if count == 0:
        logger.warning("Collection %s is empty — returning no results", collection_name)
        return {"documents": [], "metadatas": [], "distances": []}

    embedder = _get_embedder()
    embedding = embedder.encode([question], show_progress_bar=False).tolist()

    results = collection.query(
        query_embeddings=embedding,
        n_results=min(k, count),
        include=["documents", "metadatas", "distances"],
    )

    return {
        "documents": results.get("documents", [[]])[0],
        "metadatas": results.get("metadatas", [[]])[0],
        "distances": results.get("distances", [[]])[0],
    }


def remove_doc(
    doc_id: str,
    protected: bool = True,
    persist_dir: Optional[Path] = None,
) -> None:
    """
    Delete all chunks belonging to *doc_id* from a collection.

    Parameters
    ----------
    doc_id:     Document identifier to remove.
    protected:  Which collection to target.
    persist_dir: Override persist directory (used by tests).
    """
    collection_name = (
        cfg.PROTECTED_COLLECTION if protected else cfg.UNPROTECTED_COLLECTION
    )
    collection = _get_collection(collection_name, persist_dir)

    # ChromaDB supports deletion by metadata filter
    collection.delete(where={"doc_id": doc_id})
    logger.info("Removed doc_id=%s from collection=%s", doc_id, collection_name)


def count(
    protected: bool = True,
    persist_dir: Optional[Path] = None,
) -> int:
    """Return the number of embeddings in a collection."""
    collection_name = (
        cfg.PROTECTED_COLLECTION if protected else cfg.UNPROTECTED_COLLECTION
    )
    collection = _get_collection(collection_name, persist_dir)
    return collection.count()
