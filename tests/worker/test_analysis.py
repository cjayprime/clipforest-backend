"""Highlight analysis: scoring, deduplication, boundary refinement and the end-to-end pipeline."""

from __future__ import annotations
from cliprover_worker.analysis.scoring import aggregate, dedupe, iou, WEIGHTS
from cliprover_worker.analysis.models import Candidate, LlmProposal, ModelScores
from cliprover_worker.analysis.refine import refine, RefineConfig
from helpers import build_transcript
from pathlib import Path
import pytest
from cliprover_worker import errors
from cliprover_worker.analysis.llm import HeuristicLlm
from cliprover_worker.analysis.pipeline import run_analysis
from cliprover_worker.config import Settings
from cliprover_worker.transcription.base import AudioInput, ProviderContext, TranscriptResult
from cliprover_worker.transcription.normalize import normalize
from cliprover_worker.transcription.providers import MockProvider


def cand(start, end, score, completeness=5):
    scores = {k: 5 for k in WEIGHTS}
    scores["completeness"] = completeness
    return Candidate(start, end, "t", "h", "s", "r", "insight", scores, score=score)


def test_weights_match_prd():
    assert WEIGHTS == {"hook": 0.25, "clarity": 0.20, "novelty": 0.15, "emotion": 0.15, "completeness": 0.15, "shareability": 0.10}
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_aggregate_is_deterministic_and_bounded():
    assert aggregate({k: 10 for k in WEIGHTS}) == 100
    assert aggregate({k: 0 for k in WEIGHTS}) == 0
    assert aggregate({"hook": 10}) == 25
    assert aggregate({"hook": 15, "clarity": -3}) == 25  # clamped to 0-10
    assert aggregate({"hook": 8, "clarity": 7, "novelty": 6, "emotion": 5, "completeness": 9, "shareability": 4}) == 68


def test_iou():
    assert iou(0, 10, 0, 10) == 1.0
    assert iou(0, 10, 10, 20) == 0.0
    assert abs(iou(0, 10, 5, 15) - 5 / 15) < 1e-9


def test_dedupe_keeps_stronger_candidate():
    a, b, c = cand(0, 30_000, 80), cand(3_000, 32_000, 60), cand(60_000, 90_000, 50)
    kept = dedupe([b, a, c], 0.5, 90_000)
    assert kept == [a, c]


def test_dedupe_expands_when_duplicate_is_more_complete():
    strong = cand(10_000, 40_000, 80, completeness=4)
    complete = cand(12_000, 50_000, 70, completeness=9)
    kept = dedupe([strong, complete], 0.5, 90_000)
    assert len(kept) == 1
    assert (kept[0].start_ms, kept[0].end_ms) == (10_000, 50_000)


def test_dedupe_never_expands_beyond_max():
    strong = cand(0, 60_000, 80, completeness=4)
    complete = cand(10_000, 75_000, 70, completeness=9)
    kept = dedupe([strong, complete], 0.5, 70_000)
    assert (kept[0].start_ms, kept[0].end_ms) == (0, 60_000)


SENTENCES = [
    "Welcome back to the show everyone.",
    "And that is exactly why we changed everything last year.",
    "The biggest mistake we made was hiring way too fast.",
    "We learned it the hard way over two long years",
    "and it nearly cost us the entire company.",
    "Here is what we do differently now.",
    "Every hire needs a written scorecard before the first interview.",
    "Um so that is the rule we follow.",
    "It sounds simple but it changed everything for us.",
]
SEGS, WORDS = build_transcript(SENTENCES)
DURATION = SEGS[-1].end_ms + 2000


def cfg(min_ms=5_000, max_ms=60_000):
    return RefineConfig(min_ms, max_ms, 250, 350)


def seg_start(i):
    return SEGS[i].start_ms


def test_snaps_start_to_segment_beginning_without_leaking_previous_words():
    s, e = refine(seg_start(2) + 1500, SEGS[2].end_ms - 500, SEGS, WORDS, cfg(), DURATION)
    assert s <= seg_start(2)
    prev_word_end = max(w.end_ms for w in WORDS if w.end_ms <= seg_start(2))
    assert s >= prev_word_end


def test_pulls_in_previous_line_for_dangling_conjunction():
    s, _ = refine(seg_start(1), SEGS[1].end_ms, SEGS, WORDS, cfg(), DURATION)
    assert s <= seg_start(0) + 1  # "And ..." pulls in the prior line


def test_extends_end_to_complete_sentence():
    _, e = refine(seg_start(2), SEGS[3].end_ms - 200, SEGS, WORDS, cfg(), DURATION)
    assert e >= SEGS[4].end_ms  # segment 3 has no terminal punctuation; segment 4 completes it


def test_enforces_minimum_duration():
    s, e = refine(seg_start(2), SEGS[2].end_ms, SEGS, WORDS, cfg(min_ms=12_000), DURATION)
    assert e - s >= 12_000


def test_enforces_maximum_duration_on_sentence_boundary():
    s, e = refine(seg_start(2), SEGS[8].end_ms, SEGS, WORDS, cfg(max_ms=15_000), DURATION)
    assert e - s <= 15_000
    assert any(abs(e - (seg.end_ms + 350)) <= 400 or abs(e - seg.end_ms) <= 400 for seg in SEGS if seg.text.endswith("."))


def test_strips_leading_filler():
    s, _ = refine(seg_start(7), SEGS[8].end_ms, SEGS, WORDS, cfg(min_ms=3_000), DURATION)
    first = next(w for w in WORDS if w.start_ms >= seg_start(7))
    assert first.text == "Um"
    assert s > first.start_ms - 250 + 1  # starts after the filler word (minus pre-roll)


def test_results_stay_within_source_duration():
    s, e = refine(0, DURATION, SEGS, WORDS, cfg(max_ms=10 * 60_000), DURATION)
    assert 0 <= s < e <= DURATION


async def _noop(*_):
    return None


async def mock_transcript(duration_ms: int) -> TranscriptResult:
    raw = await MockProvider().transcribe(AudioInput(Path("a.mp3"), duration_ms), ProviderContext(None, _noop, _noop, seed="pipeline"))
    return normalize(raw, duration_ms, "mock")


async def test_heuristic_pipeline_produces_valid_ranked_candidates():
    s = Settings()
    duration = 12 * 60_000
    t = await mock_transcript(duration)
    result = await run_analysis(t, duration, HeuristicLlm(s), s, _noop)
    cands = result.candidates
    assert 0 < len(cands) <= s.global_shortlist
    assert result.windows == 3
    for c in cands:
        assert 0 <= c.start_ms < c.end_ms <= duration
        assert s.candidate_min_ms <= c.duration_ms <= s.candidate_max_ms
        assert 0 <= c.score <= 100
        assert c.title and c.excerpt and c.reason
        assert set(c.scores) == {"hook", "clarity", "novelty", "emotion", "completeness", "shareability"}
    assert [c.score for c in cands] == sorted((c.score for c in cands), reverse=True)
    for i, a in enumerate(cands):
        for b in cands[i + 1 :]:
            assert iou(a.start_ms, a.end_ms, b.start_ms, b.end_ms) <= s.dedupe_iou


async def test_empty_transcript_yields_zero_candidates():
    s = Settings()
    t = TranscriptResult(None, 60_000, "", [], [], "mock")
    result = await run_analysis(t, 60_000, HeuristicLlm(s), s, _noop)
    assert result.candidates == []


def proposal(start: str, end: str, score: int) -> LlmProposal:
    return LlmProposal(
        start_segment=start, end_segment=end, title="A strong moment", hook_text="hook", summary="s", reason="r",
        category="insight", scores=ModelScores(hook=score, clarity=score, novelty=score, emotion=score, completeness=score, shareability=score),
    )


class FakeLlm:
    name = "fake"
    model = "fake-1"

    def __init__(self, t: TranscriptResult, rank_error: bool = False):
        self.t = t
        self.rank_error = rank_error

    async def propose(self, window, total, duration_ms):
        ids = [s.id for s in window.segments]
        return [
            proposal(ids[2], ids[12], 8),
            proposal(ids[3], ids[13], 6),  # overlapping duplicate
            proposal("s99999", ids[5], 9),  # hallucinated segment id: ignored
        ]

    async def rank(self, shortlist):
        if self.rank_error:
            raise errors.analysis_failed("temporary", retryable=True)
        return None


async def test_duplicates_and_invalid_ids_are_dropped_and_rank_failure_is_non_fatal():
    s = Settings()
    duration = 4 * 60_000
    t = await mock_transcript(duration)
    result = await run_analysis(t, duration, FakeLlm(t, rank_error=True), s, _noop)
    assert result.proposals == 2  # the invalid id never becomes a candidate
    assert len(result.candidates) == 1
    assert result.candidates[0].score == 80
    assert result.ranked is False


async def test_misconfiguration_during_rank_is_fatal():
    class Broken(FakeLlm):
        async def rank(self, shortlist):
            raise errors.misconfigured("bad key")

        async def propose(self, window, total, duration_ms):
            ids = [s.id for s in window.segments]
            return [proposal(ids[1], ids[10], 8), proposal(ids[20], ids[30], 7)]

    s = Settings()
    t = await mock_transcript(4 * 60_000)
    with pytest.raises(errors.PipelineError):
        await run_analysis(t, 4 * 60_000, Broken(t), s, _noop)
