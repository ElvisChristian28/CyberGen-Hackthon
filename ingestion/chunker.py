"""
chunker.py – Text cleaning and overlapping chunking.

Cleans control characters, normalises Unicode (NFKC), collapses whitespace,
then chunks into overlapping windows of approximately *target_tokens* tokens.

The default token estimator counts whitespace-split words and multiplies by
1.3 (a documented heuristic for sub-word tokenisers).  A real tokeniser can
be injected via the *token_counter* parameter.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Callable, List, Optional, Sequence

from ingestion.hidden import (
    Chunk,
    DEFAULT_CHUNK_OVERLAP_TOKENS,
    DEFAULT_CHUNK_TARGET_TOKENS,
    IngestionConfig,
)

logger = logging.getLogger(__name__)

# Regex: control chars except \n and \t
_CTRL_RE = re.compile(r"[^\S\n\t]+| {2,}")
_CONTROL_CHAR_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
)

# Sentence boundary heuristic
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_PARA_RE = re.compile(r"\n{2,}")


def _default_token_count(text: str) -> int:
    """
    Approximate token count: whitespace-split words × 1.3.

    This is a documented heuristic that over-counts slightly to stay within
    context limits of sub-word tokenisers (GPT-style BPE typically produces
    ~1.3 tokens per whitespace word on English text).
    """
    words = text.split()
    return int(len(words) * 1.3)


def clean_text(text: str) -> str:
    """
    Clean *text* for chunking.

    - Remove control characters (keep ``\\n`` and ``\\t``).
    - Normalise to NFKC.
    - Collapse runs of whitespace (but preserve paragraph breaks as ``\\n``).
    """
    # Remove control chars (except \n, \t)
    text = _CONTROL_CHAR_RE.sub("", text)
    # NFKC normalisation
    text = unicodedata.normalize("NFKC", text)
    # Collapse spaces within lines (not newlines)
    lines = text.split("\n")
    cleaned_lines = [" ".join(line.split()) for line in lines]
    text = "\n".join(cleaned_lines)
    # Collapse 3+ newlines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(
    text: str,
    *,
    target_tokens: int = DEFAULT_CHUNK_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
    token_counter: Optional[Callable[[str], int]] = None,
) -> List[Chunk]:
    """
    Split *text* into overlapping chunks of approximately *target_tokens*.

    Splitting priority: paragraph boundaries → sentence boundaries → word
    boundaries.  Each chunk is a :class:`Chunk` with a contiguous 0-based
    index.

    Parameters
    ----------
    text:
        Cleaned visible text to chunk.
    target_tokens:
        Target number of tokens per chunk.
    overlap_tokens:
        Number of tokens to overlap between consecutive chunks.
    token_counter:
        Callable ``(str) -> int`` returning the token count of a string.
        Defaults to :func:`_default_token_count`.

    Returns
    -------
    list[Chunk]
        Ordered, contiguously indexed chunks.  Empty list if *text* is empty.
    """
    if token_counter is None:
        token_counter = _default_token_count

    text = text.strip()
    if not text:
        return []

    # Split into paragraphs first
    paragraphs = _PARA_RE.split(text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # Build segments: each segment is a paragraph or, if a paragraph is
    # longer than target, split further into sentences, then words.
    segments: list[str] = []
    for para in paragraphs:
        if token_counter(para) <= target_tokens:
            segments.append(para)
        else:
            # Split paragraph into sentences
            sentences = _SENTENCE_RE.split(para)
            for sent in sentences:
                if token_counter(sent) <= target_tokens:
                    segments.append(sent)
                else:
                    # Last resort: split into words and group
                    words = sent.split()
                    buf: list[str] = []
                    for w in words:
                        buf.append(w)
                        if token_counter(" ".join(buf)) >= target_tokens:
                            segments.append(" ".join(buf))
                            buf = []
                    if buf:
                        segments.append(" ".join(buf))

    if not segments:
        return []

    # Greedy packing into chunks with overlap
    chunks: list[Chunk] = []
    current_segments: list[str] = []
    current_tokens = 0

    for seg in segments:
        seg_tokens = token_counter(seg)
        if current_tokens + seg_tokens > target_tokens and current_segments:
            # Emit current chunk
            chunk_text_str = "\n\n".join(current_segments)
            chunks.append(Chunk(index=len(chunks), text=chunk_text_str))

            # Build overlap from the tail of current_segments.
            # If whole segments fit in the overlap window, carry them over.
            # If even a single segment is too large, take the last N words
            # from the final segment.
            overlap_segs: list[str] = []
            overlap_tok = 0
            for s in reversed(current_segments):
                st = token_counter(s)
                if overlap_tok + st > overlap_tokens:
                    # If we haven't collected anything yet, take a word-level
                    # tail from this segment so we always produce overlap.
                    if not overlap_segs:
                        words = s.split()
                        tail: list[str] = []
                        for w in reversed(words):
                            tail.insert(0, w)
                            if token_counter(" ".join(tail)) > overlap_tokens:
                                tail.pop(0)
                                break
                        if tail:
                            overlap_segs = [" ".join(tail)]
                            overlap_tok = token_counter(overlap_segs[0])
                    break
                overlap_segs.insert(0, s)
                overlap_tok += st

            current_segments = overlap_segs
            current_tokens = overlap_tok

        current_segments.append(seg)
        current_tokens += seg_tokens

    # Final chunk
    if current_segments:
        chunk_text_str = "\n\n".join(current_segments)
        chunks.append(Chunk(index=len(chunks), text=chunk_text_str))

    return chunks
