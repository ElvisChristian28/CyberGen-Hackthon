"""
pipeline/decision.py

Maps a numeric risk score to one of three pipeline decisions.

Thresholds (configurable via config.py):
    0  – 30   →  ALLOW
    31 – 69   →  SANITIZE
    70 – 100  →  QUARANTINE

Usage
-----
    from pipeline.decision import decide

    decision = decide(55)   # "SANITIZE"
    decision = decide(0)    # "ALLOW"
    decision = decide(70)   # "QUARANTINE"
"""
from __future__ import annotations

import logging

from pipeline.config import cfg

logger = logging.getLogger(__name__)

ALLOW = "ALLOW"
SANITIZE = "SANITIZE"
QUARANTINE = "QUARANTINE"


def decide(risk_score: int) -> str:
    """
    Return the pipeline decision for a given risk score.

    Parameters
    ----------
    risk_score:
        Integer in the range [0, 100] produced by the Detection stage.

    Returns
    -------
    str
        One of ``"ALLOW"``, ``"SANITIZE"``, or ``"QUARANTINE"``.

    Raises
    ------
    ValueError
        If ``risk_score`` is not an integer or is outside the [0, 100] range.
    TypeError
        If ``risk_score`` is not an integer type.
    """
    if not isinstance(risk_score, int):
        raise TypeError(
            f"risk_score must be an int, got {type(risk_score).__name__!r}"
        )
    if not (0 <= risk_score <= 100):
        raise ValueError(
            f"risk_score must be in [0, 100], got {risk_score}"
        )

    if risk_score <= cfg.ALLOW_MAX:
        result = ALLOW
    elif risk_score <= cfg.SANITIZE_MAX:
        result = SANITIZE
    else:
        result = QUARANTINE

    logger.debug("decide(%d) → %s", risk_score, result)
    return result
