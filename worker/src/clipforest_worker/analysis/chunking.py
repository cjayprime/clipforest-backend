"""Bounded, overlapping transcript windows (PRD §7.1-7.2). The full transcript is never
sent to a single unconstrained prompt."""

from __future__ import annotations

from dataclasses import dataclass

from ..transcription.base import Segment


@dataclass
class Window:
    index: int
    start_ms: int
    end_ms: int
    segments: list[Segment]


def make_windows(segments: list[Segment], window_ms: int, overlap_ms: int) -> list[Window]:
    if not segments:
        return []
    step = max(1, window_ms - overlap_ms)
    total_end = max(s.end_ms for s in segments)
    start = segments[0].start_ms
    windows: list[Window] = []
    while True:
        end = start + window_ms
        segs = [s for s in segments if start <= s.start_ms < end]
        if segs:
            windows.append(Window(len(windows), start, min(end, total_end), segs))
        if end >= total_end:
            break
        start += step
    return windows


def fmt_ts(ms: int) -> str:
    s = ms / 1000
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    frac = int((s - int(s)) * 10)
    return f"{h}:{m:02d}:{sec:02d}.{frac}" if h else f"{m:02d}:{sec:02d}.{frac}"


def window_text(window: Window) -> str:
    lines = []
    for s in window.segments:
        speaker = f" ({s.speaker})" if s.speaker else ""
        lines.append(f"[{s.id} | {fmt_ts(s.start_ms)}-{fmt_ts(s.end_ms)}]{speaker} {s.text}")
    return "\n".join(lines)
