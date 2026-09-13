"""Transcription: normalization, the mock provider, auto-resolution, and the HTTP providers' request/response handling."""

from __future__ import annotations
from pathlib import Path
from cliprover_worker.transcription.base import AudioInput, ProviderContext, RawTranscript, Segment, Word
from cliprover_worker.transcription.normalize import monotonic_ratio, normalize
from cliprover_worker.transcription.providers import AssemblyAIProvider, DeepgramProvider, MockProvider, resolve_provider_name, WhisperApiProvider
from cliprover_worker.config import Settings
import asyncio
import json
import httpx
import pytest
from cliprover_worker import errors


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


@pytest.fixture
def audio(tmp_path):
    p = tmp_path / "audio.mp3"
    p.write_bytes(b"ID3fake-audio-bytes" * 100)
    return AudioInput(p, 120_000)


def context(job_id: str | None = None):
    saved: list[str | None] = []

    async def save(jid):
        saved.append(jid)

    async def progress(_f):
        return None

    return ProviderContext(job_id, save, progress, seed="video-1"), saved


# Captured once: each patch must delegate to the pristine constructor, never to a previous patch.
_ORIGINAL_ASYNC_CLIENT_INIT = httpx.AsyncClient.__init__


async def run_with_transport(monkeypatch, provider, audio, ctx, handler):
    """Force the adapter's AsyncClient onto a mock transport."""

    def init(self, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        _ORIGINAL_ASYNC_CLIENT_INIT(self, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)
    return await provider.transcribe(audio, ctx)


async def test_whisper_builds_a_valid_multipart_request(monkeypatch, audio):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        body = request.content.decode("latin-1")
        seen["body"] = body
        return httpx.Response(
            200,
            json={
                "text": "Hello world.",
                "language": "en",
                "words": [{"word": "Hello", "start": 0.0, "end": 0.4}, {"word": "world.", "start": 0.4, "end": 0.9}],
                "segments": [{"id": 0, "start": 0.0, "end": 0.9, "text": "Hello world."}],
            },
        )

    ctx, _ = context()
    provider = WhisperApiProvider("test-key", "https://api.example.com/v1", "whisper-1")
    raw = await run_with_transport(monkeypatch, provider, audio, ctx, handler)

    assert seen["url"] == "https://api.example.com/v1/audio/transcriptions"
    assert seen["auth"] == "Bearer test-key"
    # Repeated multipart field must appear twice (word + segment granularities).
    assert seen["body"].count('name="timestamp_granularities[]"') == 2
    assert 'name="model"' in seen["body"] and 'name="file"' in seen["body"]
    assert [w.text for w in raw.words] == ["Hello", "world."]
    assert raw.words[1].start_ms == 400 and raw.words[1].end_ms == 900
    assert raw.language == "en" and raw.segments and raw.segments[0].text == "Hello world."


async def test_deepgram_maps_words_utterances_and_speakers(monkeypatch, audio):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["content_type"] = request.headers.get("content-type")
        return httpx.Response(
            200,
            json={
                "metadata": {"request_id": "req-1"},
                "results": {
                    "channels": [
                        {
                            "detected_language": "en",
                            "alternatives": [
                                {
                                    "words": [
                                        {"word": "hey", "punctuated_word": "Hey,", "start": 1.0, "end": 1.2, "confidence": 0.99, "speaker": 0},
                                        {"word": "there", "punctuated_word": "there.", "start": 1.2, "end": 1.5, "confidence": 0.98, "speaker": 1},
                                    ]
                                }
                            ],
                        }
                    ],
                    "utterances": [{"start": 1.0, "end": 1.5, "transcript": "Hey, there.", "speaker": 0}],
                },
            },
        )

    ctx, _ = context()
    raw = await run_with_transport(monkeypatch, DeepgramProvider("dg-key", "nova-3"), audio, ctx, handler)

    assert "model=nova-3" in seen["url"] and "diarize=true" in seen["url"]
    assert seen["auth"] == "Token dg-key" and seen["content_type"] == "audio/mpeg"
    assert [w.text for w in raw.words] == ["Hey,", "there."]
    assert raw.words[0].start_ms == 1000 and raw.words[0].speaker == "0"
    assert raw.segments and raw.segments[0].speaker == "0"
    assert raw.language == "en"


async def test_assemblyai_uploads_submits_and_polls(monkeypatch, audio):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if path == "/v2/upload":
            return httpx.Response(200, json={"upload_url": "https://cdn.example/audio"})
        if path == "/v2/transcript" and request.method == "POST":
            body = json.loads(request.content)
            assert body["audio_url"] == "https://cdn.example/audio" and body["speaker_labels"] is True
            return httpx.Response(200, json={"id": "job-42", "status": "queued"})
        if path == "/v2/transcript/job-42":
            done = calls.count("GET /v2/transcript/job-42") > 1
            return httpx.Response(
                200,
                json={
                    "status": "completed" if done else "processing",
                    "language_code": "en",
                    "words": [{"text": "Hi", "start": 100, "end": 300, "confidence": 0.9, "speaker": "A"}],
                },
            )
        if path == "/v2/transcript/job-42/sentences":
            return httpx.Response(200, json={"sentences": [{"start": 100, "end": 300, "text": "Hi", "speaker": "A"}]})
        return httpx.Response(404)

    # Skip the poll backoff without recursing into the patched sleep.
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: real_sleep(0))
    ctx, saved = context()
    raw = await run_with_transport(monkeypatch, AssemblyAIProvider("aai-key"), audio, ctx, handler)

    assert saved == ["job-42"], "the provider job id is persisted so a restart resumes"
    assert calls[0] == "POST /v2/upload"
    assert raw.words[0].text == "Hi" and raw.words[0].speaker == "A"
    assert raw.metadata["providerJobId"] == "job-42"


async def test_assemblyai_resumes_from_saved_job_without_reuploading(monkeypatch, audio):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v2/transcript/job-99":
            return httpx.Response(200, json={"status": "completed", "language_code": "en", "words": [{"text": "Resumed", "start": 0, "end": 200}]})
        if request.url.path.endswith("/sentences"):
            return httpx.Response(200, json={"sentences": []})
        return httpx.Response(500, text="should not be called")

    ctx, _ = context("job-99")
    raw = await run_with_transport(monkeypatch, AssemblyAIProvider("aai-key"), audio, ctx, handler)
    assert not any("upload" in c for c in calls)
    assert raw.words[0].text == "Resumed"


async def test_http_errors_map_to_the_retry_policy(monkeypatch, audio):
    async def call(status: int, headers: dict | None = None):
        ctx, _ = context()
        return await run_with_transport(
            monkeypatch, DeepgramProvider("k", "nova-3"), audio, ctx, lambda _r: httpx.Response(status, headers=headers or {}, text="boom")
        )

    with pytest.raises(errors.PipelineError) as rate:
        await call(429, {"retry-after": "30"})
    assert rate.value.code == "TRANSCRIPTION_RATE_LIMITED" and rate.value.retryable

    with pytest.raises(errors.PipelineError) as auth:
        await call(401)
    assert auth.value.code == "SYSTEM_MISCONFIGURED" and not auth.value.retryable

    with pytest.raises(errors.PipelineError) as server:
        await call(503)
    assert server.value.code == "TRANSCRIPTION_PROVIDER_ERROR" and server.value.retryable

    with pytest.raises(errors.PipelineError) as bad:
        await call(400)
    assert bad.value.code == "TRANSCRIPTION_PROVIDER_ERROR" and not bad.value.retryable
