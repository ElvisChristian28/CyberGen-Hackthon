"""
scripts/reset_demo.py

Wipe all persistent demo state for a clean CyberGen demo run.

Removes:
  - ChromaDB vector store (both collections)
  - quarantine.json
  - audit.jsonl

Usage
-----
    python scripts/reset_demo.py
    python scripts/reset_demo.py --confirm   # skip interactive prompt
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def reset(confirm: bool = False) -> None:
    # Import here so the script can be run from the repo root
    try:
        from pipeline.config import cfg
        from pipeline.vector_db import reset_client
    except ImportError:
        print("Error: run this script from the repo root with `python scripts/reset_demo.py`")
        sys.exit(1)

    targets = [
        cfg.CHROMA_PERSIST_DIR,
        cfg.QUARANTINE_FILE,
        cfg.AUDIT_FILE,
    ]

    print("The following will be permanently deleted:")
    for t in targets:
        status = "exists" if t.exists() else "not found"
        print(f"  {t}  ({status})")

    if not confirm:
        ans = input("\nProceed? [y/N] ").strip().lower()
        if ans != "y":
            print("Aborted.")
            return

    for target in targets:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            print(f"Removed directory: {target}")
        elif target.is_file():
            target.unlink()
            print(f"Removed file: {target}")
        else:
            print(f"Skipped (not found): {target}")

    # Recreate empty data dir
    cfg.ensure_data_dir()
    # Reset in-memory ChromaDB client so next import gets a fresh one
    reset_client()
    print("\nDemo state reset complete. Ready for a fresh run.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset CyberGen demo state.")
    parser.add_argument(
        "--confirm", action="store_true",
        help="Skip the interactive confirmation prompt"
    )
    args = parser.parse_args()
    reset(confirm=args.confirm)
