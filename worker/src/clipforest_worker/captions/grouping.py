"""Caption word selection and phrase grouping (PRD §10.1-10.2, 10.4).

Captions come from the persisted word timings (never re-transcribed). Clip-local
times are source times minus the clip start.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..transcription.base import Word

FILLERS = {"um", "uh", "erm", "er", "uhm", "hmm", "mm"}
SENTENCE_END = (".", "!", "?", "…")
_BARE = re.compile(r"[^\w']+")


@dataclass
class CaptionWord:
    start_ms: int
    end_ms: int
    text: str


@dataclass
class Phrase:
    words: list[CaptionWord]
    lines: list[list[CaptionWord]] = field(default_factory=list)
    start_ms: int = 0
    end_ms: int = 0

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def _bare(text: str) -> str:
    return _BARE.sub("", text).lower()


def clip_words(words: list[Word], clip_start_ms: int, clip_end_ms: int, remove_fillers: bool = True) -> list[CaptionWord]:
    """Selects words spoken inside the clip, converts to clip-local time and applies
    meaning-preserving cleanup (filler tokens, immediate stutter repeats)."""
    length = clip_end_ms - clip_start_ms
    out: list[CaptionWord] = []
    for w in words:
        if w.end_ms <= clip_start_ms or w.start_ms >= clip_end_ms:
            continue
        text = w.text.strip()
        if not text:
            continue
        bare = _bare(text)
        if remove_fillers and bare in FILLERS:
            continue
        if remove_fillers and out and bare and bare == _bare(out[-1].text) and w.start_ms - clip_start_ms - out[-1].end_ms < 400:
            out[-1].end_ms = max(out[-1].end_ms, min(length, w.end_ms - clip_start_ms))
            continue
        start = max(0, w.start_ms - clip_start_ms)
        end = min(length, w.end_ms - clip_start_ms)
        if end <= start:
            end = min(length, start + 1)
        out.append(CaptionWord(start, end, text))
    return out


def _balance_lines(words: list[CaptionWord], max_chars: int) -> list[list[CaptionWord]]:
    """One line if it fits; otherwise the most balanced two-line split (avoiding a lone word)."""
    total = len(" ".join(w.text for w in words))
    if total <= max_chars or len(words) < 2:
        return [words]
    best, best_cost = None, None
    for k in range(1, len(words)):
        a = len(" ".join(w.text for w in words[:k]))
        b = len(" ".join(w.text for w in words[k:]))
        cost = max(a, b) + (100 if max(a, b) > max_chars else 0) + (8 if (k == 1 or k == len(words) - 1) and len(words) > 2 else 0)
        if best_cost is None or cost < best_cost:
            best, best_cost = k, cost
    assert best is not None
    return [words[:best], words[best:]]


def group_phrases(
    words: list[CaptionWord],
    *,
    max_chars_per_line: int,
    max_words: int,
    max_lines: int = 2,
    gap_ms: int = 600,
    hold_ms: int = 300,
    clip_length_ms: int | None = None,
) -> list[Phrase]:
    phrases: list[Phrase] = []
    cur: list[CaptionWord] = []

    def flush():
        nonlocal cur
        if cur:
            phrases.append(Phrase(cur))
            cur = []

    for w in words:
        if cur:
            gap = w.start_ms - cur[-1].end_ms
            chars = len(" ".join(x.text for x in cur)) + 1 + len(w.text)
            if gap > gap_ms or len(cur) >= max_words or chars > max_chars_per_line * max_lines:
                flush()
        cur.append(w)
        if w.text.endswith(SENTENCE_END) and len(cur) >= 2:
            flush()
    flush()

    # Avoid a lone short word as its own caption when it can join the previous phrase.
    merged: list[Phrase] = []
    for p in phrases:
        if (
            merged
            and len(p.words) == 1
            and len(p.words[0].text) <= 5
            and len(merged[-1].words) < max_words + 1
            and p.words[0].start_ms - merged[-1].words[-1].end_ms <= gap_ms
            and len(merged[-1].text) + 1 + len(p.words[0].text) <= max_chars_per_line * max_lines
            and not merged[-1].words[-1].text.endswith(SENTENCE_END)
        ):
            merged[-1].words.append(p.words[0])
        else:
            merged.append(p)

    for i, p in enumerate(merged):
        p.lines = _balance_lines(p.words, max_chars_per_line)
        p.start_ms = p.words[0].start_ms  # never before the first word is spoken
        next_start = merged[i + 1].words[0].start_ms if i + 1 < len(merged) else None
        end = p.words[-1].end_ms + hold_ms
        if next_start is not None:
            end = min(end, next_start)
        if clip_length_ms is not None:
            end = min(end, clip_length_ms)
        p.end_ms = max(end, p.words[-1].end_ms if next_start is None else min(p.words[-1].end_ms, next_start), p.start_ms + 40)
    return merged
