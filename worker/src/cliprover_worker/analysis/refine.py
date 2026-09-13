"""Boundary refinement (PRD §7.6): start and end on natural speech boundaries."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

from ..transcription.base import Segment, Word

DANGLING_START = {"and", "but", "so", "or", "because", "also", "then", "which", "plus", "anyway", "cause", "nor"}
PRONOUN_START = {"he", "she", "they", "it", "this", "that", "these", "those", "him", "her", "them", "his", "their"}
FILLERS = {"um", "uh", "erm", "er", "ah", "hmm", "mm", "uhm", "like"}
TERMINAL = (".", "!", "?", "…", '."', '!"', '?"')
_WORD = re.compile(r"[A-Za-z']+")


@dataclass
class RefineConfig:
    min_ms: int
    max_ms: int
    pre_roll_ms: int
    post_roll_ms: int


def _first_token(text: str) -> str:
    m = _WORD.search(text)
    return m.group(0).lower() if m else ""


def _is_terminal(text: str) -> bool:
    return text.rstrip().endswith(TERMINAL)


def refine(start_ms: int, end_ms: int, segments: list[Segment], words: list[Word], cfg: RefineConfig, duration_ms: int) -> tuple[int, int] | None:
    if not segments:
        return None
    starts = [s.start_ms for s in segments]
    n = len(segments)

    # Snap start to the beginning of the segment containing it (or the next one if it's in a gap).
    si = max(0, bisect.bisect_right(starts, start_ms + 400) - 1)
    if segments[si].end_ms <= start_ms and si + 1 < n:
        si += 1
    # Snap end to the end of the segment containing it.
    ei = max(si, bisect.bisect_right(starts, max(start_ms, end_ms - 400)) - 1)

    def dur(a: int, b: int) -> int:
        return segments[b].end_ms - segments[a].start_ms

    # Avoid starting on a dangling conjunction or a pronoun without referent: pull in one or two prior lines.
    for _ in range(2):
        tok = _first_token(segments[si].text)
        if (tok in DANGLING_START or tok in PRONOUN_START) and si > 0 and dur(si - 1, ei) <= cfg.max_ms:
            si -= 1
        else:
            break

    # Prefer ending after a complete sentence / payoff.
    while not _is_terminal(segments[ei].text) and ei + 1 < n and dur(si, ei + 1) <= cfg.max_ms:
        ei += 1

    # Enforce the minimum duration by extending forward, then backward.
    while dur(si, ei) < cfg.min_ms and ei + 1 < n and dur(si, ei + 1) <= cfg.max_ms:
        ei += 1
    while dur(si, ei) < cfg.min_ms and si > 0 and dur(si - 1, ei) <= cfg.max_ms:
        si -= 1

    # Enforce the maximum by trimming the end back, preferring a sentence boundary.
    if dur(si, ei) > cfg.max_ms:
        fitting = [e for e in range(si, ei + 1) if dur(si, e) <= cfg.max_ms]
        terminal = [e for e in fitting if _is_terminal(segments[e].text)]
        ei = (terminal or fitting or [si])[-1]

    s, e = segments[si].start_ms, segments[ei].end_ms

    # A single overlong segment: cut at the last word that fits.
    if e - s > cfg.max_ms:
        fit = [w.end_ms for w in words if s <= w.start_ms and w.end_ms <= s + cfg.max_ms]
        e = max(fit) if fit else s + cfg.max_ms

    # Drop leading filler words ("um", "uh") inside the first line.
    lead = [w for w in words if s <= w.start_ms < e]
    for w in lead:
        if _first_token(w.text) in FILLERS and len(_WORD.findall(w.text)) == 1:
            continue
        s = max(s, w.start_ms)
        break

    # Small pre/post roll, bounded by neighbouring words so no stray syllables leak in.
    word_starts = [w.start_ms for w in words]
    i = bisect.bisect_left(word_starts, s)
    prev_end = words[i - 1].end_ms if i > 0 else 0
    s = max(0, max(s - cfg.pre_roll_ms, min(prev_end, s)))
    j = bisect.bisect_left(word_starts, e)
    next_start = words[j].start_ms if j < len(words) else duration_ms
    e = min(duration_ms, min(e + cfg.post_roll_ms, max(next_start, e)))

    if e - s > cfg.max_ms:
        e = s + cfg.max_ms
    total_speech = segments[-1].end_ms - segments[0].start_ms
    if e - s < cfg.min_ms and total_speech >= cfg.min_ms:
        return None
    if e <= s:
        return None
    return int(s), int(e)


def excerpt_for(words: list[Word], start_ms: int, end_ms: int, limit: int = 600) -> str:
    text = " ".join(w.text for w in words if w.start_ms >= start_ms and w.end_ms <= end_ms + 50)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "…"
