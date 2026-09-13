from __future__ import annotations

from cliprover_worker.transcription.base import Segment, Word


def words_for(text: str, start_ms: int, word_ms: int = 280, gap_ms: int = 60) -> list[Word]:
    out = []
    t = start_ms
    for token in text.split():
        out.append(Word(t, t + word_ms, token))
        t += word_ms + gap_ms
    return out


def build_transcript(sentences: list[str], start_ms: int = 0, pause_ms: int = 500) -> tuple[list[Segment], list[Word]]:
    segments: list[Segment] = []
    words: list[Word] = []
    t = start_ms
    for i, s in enumerate(sentences):
        ws = words_for(s, t)
        words += ws
        segments.append(Segment(f"s{i}", ws[0].start_ms, ws[-1].end_ms, s))
        t = ws[-1].end_ms + pause_ms
    return segments, words
