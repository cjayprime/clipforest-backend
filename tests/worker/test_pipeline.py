from pathlib import Path

import pytest

from clipforest_worker import errors
from clipforest_worker.analysis.dedupe import iou
from clipforest_worker.analysis.llm import HeuristicLlm
from clipforest_worker.analysis.models import LlmProposal, ModelScores
from clipforest_worker.analysis.pipeline import run_analysis
from clipforest_worker.config import Settings
from clipforest_worker.transcription.base import AudioInput, ProviderContext, TranscriptResult
from clipforest_worker.transcription.normalize import normalize
from clipforest_worker.transcription.providers import MockProvider


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
