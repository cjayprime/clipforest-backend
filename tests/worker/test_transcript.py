from pathlib import Path

from clipforest_worker.transcription.base import AudioInput, ProviderContext, RawTranscript, Segment, Word
from clipforest_worker.transcription.normalize import monotonic_ratio, normalize
from clipforest_worker.transcription.providers import MockProvider, resolve_provider_name
from clipforest_worker.config import Settings


def test_normalize_fixes_timings_and_builds_segments():
    raw = RawTranscript(
        words=[
            Word(-50, 200, "Hello"),
            Word(150, 500, "world."),  # overlaps previous word
            Word(2400, 2300, "This"),  # end before start
            Word(2500, 2800, "is"),
            Word(2450, 3000, "fine."),  # non-monotonic start
            Word(9000, 99_999, "late"),  # beyond duration
            Word(9500, 9600, "  "),  # empty
        ],
        segments=None,
        language="en",
    )
    t = normalize(raw, 10_000, "test")
    # Out-of-order words are re-sorted by start time (the monotonic fix), empty tokens dropped.
    assert [w.text for w in t.words] == ["Hello", "world.", "This", "fine.", "is", "late"]
    assert monotonic_ratio(t.words) == 1.0
    assert all(0 <= w.start_ms <= w.end_ms <= 10_000 for w in t.words)
    for a, b in zip(t.words, t.words[1:]):
        assert a.end_ms <= b.start_ms or a.end_ms == a.start_ms
    assert [s.id for s in t.segments] == [f"s{i}" for i in range(len(t.segments))]
    assert t.segments[0].text == "Hello world."
    assert t.provider_metadata["timingFixes"] >= 3
    assert t.full_text.startswith("Hello world.")


def test_long_provider_segments_are_split():
    words = [Word(i * 500, i * 500 + 400, f"w{i}") for i in range(120)]  # 60 s, no punctuation
    raw = RawTranscript(words=words, segments=[Segment("", 0, 60_000, "x")], language=None)
    t = normalize(raw, 60_000, "test")
    assert len(t.segments) >= 3
    assert all(s.end_ms - s.start_ms <= 21_000 for s in t.segments)


async def _noop(*_):
    return None


async def test_mock_provider_is_deterministic_and_covers_duration():
    ctx = ProviderContext(None, _noop, _noop, seed="video-1")
    a = await MockProvider().transcribe(AudioInput(Path("x.mp3"), 120_000), ctx)
    b = await MockProvider().transcribe(AudioInput(Path("x.mp3"), 120_000), ctx)
    assert [w.text for w in a.words] == [w.text for w in b.words]
    assert a.words[-1].end_ms <= 120_000
    assert a.words[-1].end_ms > 100_000
    t = normalize(a, 120_000, "mock")
    assert monotonic_ratio(t.words) >= 0.99


def test_provider_auto_resolution(monkeypatch):
    for k in ("ASSEMBLYAI_API_KEY", "DEEPGRAM_API_KEY", "WHISPER_API_KEY", "TRANSCRIPTION_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    assert resolve_provider_name(Settings()) == "mock"
    monkeypatch.setenv("DEEPGRAM_API_KEY", "x")
    assert resolve_provider_name(Settings()) == "deepgram"
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "local-whisper")
    assert resolve_provider_name(Settings()) == "local-whisper"
