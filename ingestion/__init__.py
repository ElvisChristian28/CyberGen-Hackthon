"""
ingestion – Stage 1 of the prompt-injection defense pipeline.

Public API::

    from ingestion.loader import ingest
    result = ingest("path/to/document.pdf")
"""

from ingestion.loader import ingest
from ingestion.hidden import IngestionConfig, validate_output

__all__ = ["ingest", "IngestionConfig", "validate_output"]
