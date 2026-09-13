"""Provider-agnostic transcript normalization.

Guarantees for downstream analysis and captions:
  * word timings are integer ms, non-negative, within the source duration and monotonic
  * segments are sentence-like, at most ~20 s, each with a stable id ("s0", "s1", ...)
"""

from __future__ import annotations

import re

from .base import RawTranscript, Segment, TranscriptResult, Word

TERMINAL = (".", "!", "?", "…")
MAX_SEGMENT_MS = 20_000
MAX_SEGMENT_WORDS = 55
GAP_SPLIT_MS = 1_200


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_words(words: list[Word], duration_ms: int) -> tuple[list[Word], int]:
    """Returns (clean words, number of timing fixes applied)."""
    fixes = 0
    out: list[Word] = []
    for w in sorted((w for w in words if _clean_text(w.text)), key=lambda w: (w.start_ms, w.end_ms)):
        start, end = int(w.start_ms), int(w.end_ms)
        if start < 0:
            start, fixes = 0, fixes + 1
        if duration_ms and end > duration_ms:
            end, fixes = duration_ms, fixes + 1
        if duration_ms and start > duration_ms:
            start, fixes = duration_ms, fixes + 1
        if end < start:
            end, fixes = start, fixes + 1
        if out:
            prev = out[-1]
            if start < prev.start_ms:
                start, fixes = prev.start_ms, fixes + 1
                end = max(end, start)
            if prev.end_ms > start:
                prev.end_ms = start
                fixes += 1
        out.append(Word(start, end, _clean_text(w.text), w.confidence, w.speaker))
    return out, fixes


def _majority_speaker(words: list[Word]) -> str | None:
    counts: dict[str, int] = {}
    for w in words:
        if w.speaker is not None:
            counts[w.speaker] = counts.get(w.speaker, 0) + 1
    return max(counts, key=counts.get) if counts else None


def segments_from_words(words: list[Word]) -> list[list[Word]]:
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            gap = w.start_ms - cur[-1].end_ms
            too_long = w.end_ms - cur[0].start_ms > MAX_SEGMENT_MS or len(cur) >= MAX_SEGMENT_WORDS
            speaker_change = w.speaker is not None and cur[-1].speaker is not None and w.speaker != cur[-1].speaker
            if gap > GAP_SPLIT_MS or too_long or speaker_change:
                groups.append(cur)
                cur = []
        cur.append(w)
        if w.text.endswith(TERMINAL) and len(cur) >= 3:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def normalize(raw: RawTranscript, duration_ms: int, provider: str) -> TranscriptResult:
    words, fixes = normalize_words(raw.words, duration_ms)
    groups: list[list[Word]]
    if raw.segments:
        # Re-derive provider segments from our cleaned words so text and timings agree,
        # then split any overly long provider segment.
        groups = []
        bounds = sorted((s.start_ms, s.end_ms) for s in raw.segments)
        wi = 0
        for s_start, s_end in bounds:
            g: list[Word] = []
            while wi < len(words) and words[wi].start_ms < s_end:
                if words[wi].start_ms >= s_start - 50 or not g:
                    g.append(words[wi])
                wi += 1
            if g:
                groups.extend(segments_from_words(g) if (g[-1].end_ms - g[0].start_ms > MAX_SEGMENT_MS) else [g])
        if wi < len(words):
            groups.extend(segments_from_words(words[wi:]))
    else:
        groups = segments_from_words(words)

    segments = [
        Segment(
            id=f"s{i}",
            start_ms=g[0].start_ms,
            end_ms=max(g[-1].end_ms, g[0].start_ms + 1),
            text=" ".join(w.text for w in g),
            speaker=_majority_speaker(g),
        )
        for i, g in enumerate(groups)
        if g
    ]
    meta = dict(raw.metadata)
    meta["timingFixes"] = fixes
    meta["wordCount"] = len(words)
    return TranscriptResult(
        language=raw.language,
        duration_ms=duration_ms,
        full_text=" ".join(s.text for s in segments),
        segments=segments,
        words=words,
        provider=provider,
        provider_metadata=meta,
    )


def monotonic_ratio(words: list[Word]) -> float:
    """Share of words with non-negative, monotonic timings (acceptance: >= 0.99)."""
    if not words:
        return 1.0
    ok = 0
    prev = -1
    for w in words:
        if w.start_ms >= 0 and w.end_ms >= w.start_ms and w.start_ms >= prev:
            ok += 1
        prev = w.start_ms
    return ok / len(words)
