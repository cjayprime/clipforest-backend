"""Adapter-level contract tests for the hosted transcription providers.

They run against httpx MockTransport (no network, no keys), and assert that each
adapter builds a well-formed request and maps the provider's response onto the
normalized schema — including the async-resume path and HTTP error mapping.
"""

import asyncio
import json

import httpx
import pytest

from clipforest_worker import errors
from clipforest_worker.transcription.base import AudioInput, ProviderContext
from clipforest_worker.transcription.providers import AssemblyAIProvider, DeepgramProvider, WhisperApiProvider


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
