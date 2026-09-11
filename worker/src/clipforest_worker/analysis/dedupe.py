"""Temporal IoU deduplication (PRD §7.5)."""

from __future__ import annotations

from .models import Candidate


def iou(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    inter = max(0, min(a_end, b_end) - max(a_start, b_start))
    union = max(a_end, b_end) - min(a_start, b_start)
    return inter / union if union > 0 else 0.0


def dedupe(candidates: list[Candidate], threshold: float, max_ms: int) -> list[Candidate]:
    """Keeps the stronger of any pair whose ranges overlap above `threshold`.

    When the weaker duplicate is judged more complete, the kept candidate's range
    expands to the union (if that stays within `max_ms`) so the payoff isn't lost.
    """
    kept: list[Candidate] = []
    for c in sorted(candidates, key=lambda c: (-c.score, c.start_ms)):
        dup = next((k for k in kept if iou(k.start_ms, k.end_ms, c.start_ms, c.end_ms) > threshold), None)
        if dup is None:
            kept.append(c)
            continue
        union_start, union_end = min(dup.start_ms, c.start_ms), max(dup.end_ms, c.end_ms)
        if c.scores.get("completeness", 0) > dup.scores.get("completeness", 0) and union_end - union_start <= max_ms:
            dup.start_ms, dup.end_ms = union_start, union_end
            dup.notes.append("expanded-by-duplicate")
    return kept
