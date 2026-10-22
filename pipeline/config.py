"""
pipeline/config.py

Central configuration for the Pipeline stage.
All tuneable values, file paths, and environment-variable lookups live here.
Nothing else in the package hardcodes paths or secrets.

Usage
-----
    from pipeline.config import cfg

    print(cfg.ALLOW_MAX)          # 30
    print(cfg.CHROMA_PERSIST_DIR) # resolved absolute path
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ── Locate repo root ─────────────────────────────────────────────────────────
# config.py lives at  <repo_root>/pipeline/config.py
_PIPELINE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PIPELINE_DIR.parent

# Load .env from repo root (silently ignore if absent)
load_dotenv(_REPO_ROOT / ".env")


class _Config:
    """
    All configuration in one place.
    Attribute access is cheap; no singleton magic needed.
    """

    # ── Risk-score thresholds ────────────────────────────────────────────
    ALLOW_MAX: int = 30       # 0–30   → ALLOW
    SANITIZE_MAX: int = 69    # 31–69  → SANITIZE  (70–100 → QUARANTINE)

    # ── Directory / file paths ───────────────────────────────────────────
    DATA_DIR: Path = _PIPELINE_DIR / "data"
    CHROMA_PERSIST_DIR: Path = DATA_DIR / "chroma"
    QUARANTINE_FILE: Path = DATA_DIR / "quarantine.json"
    AUDIT_FILE: Path = DATA_DIR / "audit.jsonl"

    # ── ChromaDB collection names ────────────────────────────────────────
    PROTECTED_COLLECTION: str = "cybergen_docs"
    UNPROTECTED_COLLECTION: str = "unprotected_docs"

    # ── Embedding model (sentence-transformers) ──────────────────────────
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    # ── LLM settings (read from environment) ────────────────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-pro")

    # ── RAG retrieval ────────────────────────────────────────────────────
    RAG_TOP_K: int = 3   # number of chunks retrieved per query

    def ensure_data_dir(self) -> None:
        """Create data directory and its parents if they don't exist."""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)


cfg = _Config()
