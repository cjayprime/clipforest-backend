"""Highlight discovery: chunk -> generate -> normalize -> dedupe -> refine -> rank (PRD §7)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..config import Settings
from ..errors import PipelineError
from ..log import get_logger
from ..transcription.base import TranscriptResult
from .chunking import make_windows
from .scoring import dedupe
from .llm import LlmProvider
from .models import Candidate
from .refine import RefineConfig, excerpt_for, refine
from .scoring import aggregate, clamp_scores

log = get_logger(__name__)


@dataclass
class AnalysisResult:
    candidates: list[Candidate]
    windows: int
    proposals: int
    provider: str
    model: str
    ranked: bool


async def run_analysis(
    transcript: TranscriptResult,
    duration_ms: int,
    llm: LlmProvider,
    s: Settings,
    on_progress: Callable[[float], Awaitable[None]],
) -> AnalysisResult:
    segments, words = transcript.segments, transcript.words
    windows = make_windows(segments, s.analysis_window_ms, s.analysis_overlap_ms)
    if not windows:
        return AnalysisResult([], 0, 0, llm.name, llm.model, False)

    index_of = {seg.id: i for i, seg in enumerate(segments)}
    sem = asyncio.Semaphore(max(1, s.llm_max_concurrency))
    done = 0

    async def run_window(w):
        nonlocal done
        async with sem:
            proposals = await llm.propose(w, len(windows), duration_ms)
        done += 1
        await on_progress(done / len(windows) * 0.85)
        return w, proposals

    results = await asyncio.gather(*(run_window(w) for w in windows))

    # Normalize proposals to source time; drop anything referencing segments outside its window.
    raw: list[Candidate] = []
    for w, proposals in results:
        in_window = {seg.id for seg in w.segments}
        for p in proposals[: s.candidates_per_window]:
            if p.start_segment not in in_window or p.end_segment not in in_window:
                continue
            a, b = sorted((index_of[p.start_segment], index_of[p.end_segment]))
            start = max(0, segments[a].start_ms)
            end = min(duration_ms, segments[b].end_ms)
            if end <= start:
                continue
            scores = clamp_scores(p.scores.model_dump())
            title = p.title.strip() or " ".join(p.hook_text.split()[:8])
            raw.append(
                Candidate(
                    start_ms=start,
                    end_ms=end,
                    title=title[:80],
                    hook_text=p.hook_text.strip()[:500],
                    summary=p.summary.strip(),
                    reason=p.reason.strip(),
                    category=p.category,
                    scores=scores,
                    score=aggregate(scores),
                    window=w.index,
                )
            )

    rc = RefineConfig(s.candidate_min_ms, s.candidate_max_ms, s.pre_roll_ms, s.post_roll_ms)
    refined: list[Candidate] = []
    for c in dedupe(raw, s.dedupe_iou, s.candidate_max_ms):
        bounds = refine(c.start_ms, c.end_ms, segments, words, rc, duration_ms)
        if bounds is None:
            continue
        c.start_ms, c.end_ms = bounds
        refined.append(c)
    # Refinement can move ranges into each other, so deduplicate again.
    deduped = dedupe(refined, s.dedupe_iou, s.candidate_max_ms)

    shortlist = sorted(deduped, key=lambda c: (-c.score, c.start_ms))[: max(s.global_shortlist * 2, s.global_shortlist)]
    for i, c in enumerate(shortlist):
        c.key = f"c{i + 1}"
        c.excerpt = excerpt_for(words, c.start_ms, c.end_ms)

    # Second pass: calibrated, list-relative re-scoring. Non-fatal if it fails transiently.
    ranked = False
    if len(shortlist) > 1:
        try:
            rescored = await llm.rank(shortlist)
        except PipelineError as exc:
            if not exc.retryable and exc.code == "SYSTEM_MISCONFIGURED":
                raise
            log.warning("Ranking pass failed; keeping first-pass scores", extra={"code": exc.code})
            rescored = None
        if rescored:
            ranked = True
            for c in shortlist:
                item = rescored.get(c.key)
                if not item:
                    continue
                c.scores = clamp_scores(item.scores.model_dump())
                c.score = aggregate(c.scores)
                c.title = (item.title.strip() or c.title)[:80]
                c.reason = item.reason.strip() or c.reason
    await on_progress(0.95)

    final = sorted(shortlist, key=lambda c: (-c.score, c.start_ms))[: s.global_shortlist]
    for c in final:
        assert 0 <= c.start_ms < c.end_ms <= duration_ms
    return AnalysisResult(final, len(windows), len(raw), llm.name, llm.model, ranked)
