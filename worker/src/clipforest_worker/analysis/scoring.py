"""Deterministic virality score (PRD §7.7): a weighted aggregate of model-assessed
dimensions, reported 0-100. It is a ranking signal, not a performance prediction."""

from __future__ import annotations

WEIGHTS: dict[str, float] = {
    "hook": 0.25,
    "clarity": 0.20,
    "novelty": 0.15,
    "emotion": 0.15,
    "completeness": 0.15,
    "shareability": 0.10,
}


def clamp_scores(scores: dict[str, float | int]) -> dict[str, int]:
    return {k: int(max(0, min(10, round(float(scores.get(k, 0) or 0))))) for k in WEIGHTS}


def aggregate(scores: dict[str, float | int]) -> int:
    s = clamp_scores(scores)
    return int(round(sum(WEIGHTS[k] * s[k] for k in WEIGHTS) * 10))
