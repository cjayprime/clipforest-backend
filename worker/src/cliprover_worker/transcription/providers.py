"""Transcription provider adapters (PRD §6).

  assemblyai     hosted, asynchronous; persists the provider job ID and resumes after restarts
  deepgram       hosted, synchronous pre-recorded API
  whisper-api    hosted, OpenAI-compatible /audio/transcriptions (auto-chunks >24 MB audio)
  local-whisper  optional local faster-whisper on CPU/int8 (serialized with renders)
  mock           deterministic fixture for local end-to-end development without paid calls
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from pathlib import Path

import httpx

from .. import errors
from ..config import Settings
from ..log import get_logger
from ..ffmpeg import probe, run_ffmpeg
from .base import AudioInput, ProviderContext, RawTranscript, Segment, TranscriptionProvider, Word, raise_for_provider_status

log = get_logger(__name__)


def _ms(seconds: float | int | None) -> int:
    return int(round(float(seconds or 0) * 1000))


class AssemblyAIProvider:
    name = "assemblyai"
    heavy_cpu = False
    BASE = "https://api.assemblyai.com/v2"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def transcribe(self, audio: AudioInput, ctx: ProviderContext) -> RawTranscript:
        timeout = httpx.Timeout(60.0, read=600.0)
        async with httpx.AsyncClient(timeout=timeout, headers={"authorization": self.api_key}) as client:
            job_id = ctx.provider_job_id
            if job_id:
                log.info("Resuming AssemblyAI job", extra={"providerJobId": job_id})
            else:
                data = await asyncio.to_thread(audio.path.read_bytes)
                resp = await client.post(f"{self.BASE}/upload", content=data)
                raise_for_provider_status(resp, self.name)
                body: dict = {"audio_url": resp.json()["upload_url"], "speaker_labels": True}
                if audio.language:
                    body["language_code"] = audio.language
                else:
                    body["language_detection"] = True
                resp = await client.post(f"{self.BASE}/transcript", json=body)
                raise_for_provider_status(resp, self.name)
                job_id = resp.json()["id"]
                await ctx.save_job_id(job_id)

            delay, waited = 3.0, 0.0
            expected = max(60.0, audio.duration_ms / 1000 * 0.3)
            while True:
                resp = await client.get(f"{self.BASE}/transcript/{job_id}")
                raise_for_provider_status(resp, self.name)
                data = resp.json()
                status = data.get("status")
                if status == "completed":
                    break
                if status == "error":
                    await ctx.save_job_id(None)  # a retry must resubmit, not re-poll a failed job
                    msg = str(data.get("error") or "unknown error")
                    if "no spoken audio" in msg.lower() or "does not appear to contain audio" in msg.lower():
                        raise errors.transcription_no_speech()
                    raise errors.transcription_failed(f"assemblyai: {msg}", retryable=True)
                await ctx.on_progress(min(0.95, waited / expected))
                await asyncio.sleep(delay)
                waited += delay
                delay = min(15.0, delay * 1.5)
                if waited > 4 * 3600:
                    raise errors.transcription_failed("assemblyai job did not finish within 4h", retryable=True)

            resp = await client.get(f"{self.BASE}/transcript/{job_id}/sentences")
            sentences = resp.json().get("sentences", []) if resp.status_code < 400 else []

        words = [
            Word(int(w["start"]), int(w["end"]), w["text"], w.get("confidence"), w.get("speaker"))
            for w in data.get("words") or []
        ]
        segments = [Segment("", int(s["start"]), int(s["end"]), s["text"], s.get("speaker")) for s in sentences] or None
        return RawTranscript(
            words=words,
            segments=segments,
            language=data.get("language_code") or audio.language,
            metadata={"providerJobId": job_id, "audioDuration": data.get("audio_duration")},
        )


class DeepgramProvider:
    name = "deepgram"
    heavy_cpu = False
    URL = "https://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def transcribe(self, audio: AudioInput, ctx: ProviderContext) -> RawTranscript:
        params = {"model": self.model, "smart_format": "true", "punctuate": "true", "utterances": "true", "diarize": "true"}
        if audio.language:
            params["language"] = audio.language
        else:
            params["detect_language"] = "true"
        data_bytes = await asyncio.to_thread(audio.path.read_bytes)
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=3600.0)) as client:
            resp = await client.post(
                self.URL,
                params=params,
                content=data_bytes,
                headers={"Authorization": f"Token {self.api_key}", "Content-Type": "audio/mpeg"},
            )
        raise_for_provider_status(resp, self.name)
        data = resp.json()
        channel = data["results"]["channels"][0]
        alt = channel["alternatives"][0]
        words = [
            Word(_ms(w["start"]), _ms(w["end"]), w.get("punctuated_word") or w["word"], w.get("confidence"),
                 str(w["speaker"]) if w.get("speaker") is not None else None)
            for w in alt.get("words") or []
        ]
        utterances = data["results"].get("utterances") or []
        segments = [
            Segment("", _ms(u["start"]), _ms(u["end"]), u.get("transcript", ""), str(u.get("speaker")) if u.get("speaker") is not None else None)
            for u in utterances
        ] or None
        return RawTranscript(
            words=words,
            segments=segments,
            language=channel.get("detected_language") or audio.language,
            metadata={"requestId": data.get("metadata", {}).get("request_id"), "model": self.model},
        )


class WhisperApiProvider:
    """OpenAI-compatible transcription endpoint (verbose_json with word + segment timestamps)."""

    name = "whisper-api"
    heavy_cpu = False
    MAX_BYTES = 24 * 1024 * 1024

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def _one(self, client: httpx.AsyncClient, path: Path, language: str | None) -> dict:
        # httpx treats a non-dict `data` as raw content, which an AsyncClient refuses to send;
        # repeated multipart fields are expressed as a list value instead.
        data: dict[str, object] = {
            "model": self.model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": ["word", "segment"],
        }
        if language:
            data["language"] = language
        content = await asyncio.to_thread(path.read_bytes)
        resp = await client.post(
            f"{self.base_url}/audio/transcriptions",
            data=data,
            files={"file": (path.name, content, "audio/mpeg")},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        raise_for_provider_status(resp, self.name)
        return resp.json()

    async def transcribe(self, audio: AudioInput, ctx: ProviderContext) -> RawTranscript:
        size = audio.path.stat().st_size
        chunks: list[Path] = [audio.path]
        if size > self.MAX_BYTES:
            seconds = max(60, int(audio.duration_ms / 1000 * (self.MAX_BYTES / size) * 0.9))
            pattern = audio.path.parent / "chunk_%03d.mp3"
            await run_ffmpeg(["-i", str(audio.path), "-f", "segment", "-segment_time", str(seconds), "-c", "copy", str(pattern)])
            chunks = sorted(audio.path.parent.glob("chunk_*.mp3"))
        words: list[Word] = []
        segments: list[Segment] = []
        language = audio.language
        offset = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=1800.0)) as client:
            for i, chunk in enumerate(chunks):
                data = await self._one(client, chunk, audio.language)
                language = language or data.get("language")
                words += [Word(offset + _ms(w["start"]), offset + _ms(w["end"]), w["word"]) for w in data.get("words") or []]
                segments += [Segment("", offset + _ms(s["start"]), offset + _ms(s["end"]), s.get("text", "")) for s in data.get("segments") or []]
                offset += (await probe(str(chunk))).duration_ms if len(chunks) > 1 else 0
                await ctx.on_progress((i + 1) / len(chunks))
        return RawTranscript(words=words, segments=segments or None, language=language, metadata={"model": self.model, "chunks": len(chunks)})


class LocalWhisperProvider:
    """faster-whisper on CPU/int8. Opt-in only; serialized with renders via the heavy-CPU gate."""

    name = "local-whisper"
    heavy_cpu = True
    _model = None

    def __init__(self, model_size: str):
        self.model_size = model_size

    async def transcribe(self, audio: AudioInput, ctx: ProviderContext) -> RawTranscript:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise errors.misconfigured("local-whisper requires `pip install -r requirements-local-whisper.txt`") from exc

        def _run():
            if LocalWhisperProvider._model is None:
                LocalWhisperProvider._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            segs, info = LocalWhisperProvider._model.transcribe(
                str(audio.path), word_timestamps=True, vad_filter=True, language=audio.language
            )
            out_words, out_segs = [], []
            for s in segs:
                out_segs.append(Segment("", _ms(s.start), _ms(s.end), s.text.strip()))
                out_words += [Word(_ms(w.start), _ms(w.end), w.word.strip(), getattr(w, "probability", None)) for w in (s.words or [])]
            return out_words, out_segs, info.language

        words, segments, language = await asyncio.to_thread(_run)
        return RawTranscript(words=words, segments=segments or None, language=language, metadata={"model": self.model_size})


# A small creator-style corpus with hooks, numbers, questions and payoffs, so the
# heuristic analyzer has realistic material during local development.
_MOCK_CORPUS = [
    "Here's the thing nobody tells you about growing a channel.",
    "The biggest mistake I made was posting every single day without a plan.",
    "We went from zero to one hundred thousand subscribers in eleven months.",
    "So what actually changed?",
    "Honestly, it came down to three simple rules.",
    "Rule number one: your first sentence has to earn the second one.",
    "Rule number two: cut every pause that doesn't carry meaning.",
    "Rule number three: end on a payoff, never on a shrug.",
    "I remember the night our first video crossed a million views.",
    "My co-founder called me at 2am, and we just laughed for ten minutes.",
    "But here's where it gets interesting.",
    "Most creators spend ninety percent of their time editing and ten percent on ideas.",
    "Flip that ratio and everything changes.",
    "You don't need a better camera. You need a better first line.",
    "Let me give you a real example from last week.",
    "We tested two thumbnails for the same video.",
    "One had a face, one had a bold question.",
    "The question won by forty two percent.",
    "Why? Because curiosity beats recognition every time.",
    "Now, this might sound controversial, but consistency is overrated.",
    "Quality with a clear promise beats volume without one.",
    "And that's the lesson I wish I'd learned on day one.",
    "Okay, let's talk about money for a second.",
    "Our first sponsor paid us five hundred dollars.",
    "Last month, a single deal paid more than our entire first year.",
    "The secret wasn't the audience size. It was the audience trust.",
    "If you remember one thing from this episode, remember this.",
    "People share what makes them look smart to their friends.",
    "So give them something worth quoting.",
    "Anyway, that's enough theory. Let's look at the numbers.",
    "Retention dropped at exactly the thirty second mark on every video.",
    "That's where we buried the payoff, and that's where people left.",
    "Once we moved the payoff to the first ten seconds, retention doubled.",
    "It sounds obvious. It wasn't obvious to us for two years.",
    "Um, so, yeah, that's kind of where we're at right now.",
    "What would you do differently if you started today?",
    "I'd start smaller, move faster, and publish before I felt ready.",
    "Perfection is just fear wearing a nicer outfit.",
]


class MockProvider:
    """Deterministic synthetic transcript sized to the audio duration (no network, no cost)."""

    name = "mock"
    heavy_cpu = False

    async def transcribe(self, audio: AudioInput, ctx: ProviderContext) -> RawTranscript:
        rng = random.Random(hashlib.sha256((ctx.seed or str(audio.path)).encode()).hexdigest())
        words: list[Word] = []
        t = 400
        i = rng.randrange(len(_MOCK_CORPUS))
        speaker = "A"
        while t < audio.duration_ms - 800:
            sentence = _MOCK_CORPUS[i % len(_MOCK_CORPUS)]
            i += 1
            if rng.random() < 0.18:
                speaker = "B" if speaker == "A" else "A"
            for token in sentence.split(" "):
                dur = 140 + 38 * len(token) + rng.randint(-20, 40)
                if t + dur > audio.duration_ms - 200:
                    break
                words.append(Word(t, t + dur, token, round(0.9 + rng.random() * 0.1, 3), speaker))
                t += dur + rng.randint(40, 110)
            t += rng.randint(250, 900)
        await ctx.on_progress(1.0)
        return RawTranscript(words=words, segments=None, language="en", metadata={"synthetic": True})


def resolve_provider_name(s: Settings) -> str:
    if s.transcription_provider and s.transcription_provider != "auto":
        return s.transcription_provider
    if s.assemblyai_api_key:
        return "assemblyai"
    if s.deepgram_api_key:
        return "deepgram"
    if s.whisper_api_key:
        return "whisper-api"
    return "mock"


def get_provider(name: str, s: Settings) -> TranscriptionProvider:
    if name == "assemblyai":
        if not s.assemblyai_api_key:
            raise errors.misconfigured("ASSEMBLYAI_API_KEY is not set")
        return AssemblyAIProvider(s.assemblyai_api_key)
    if name == "deepgram":
        if not s.deepgram_api_key:
            raise errors.misconfigured("DEEPGRAM_API_KEY is not set")
        return DeepgramProvider(s.deepgram_api_key, s.deepgram_model)
    if name == "whisper-api":
        if not s.whisper_api_key:
            raise errors.misconfigured("WHISPER_API_KEY is not set")
        return WhisperApiProvider(s.whisper_api_key, s.whisper_api_base, s.whisper_api_model)
    if name == "local-whisper":
        return LocalWhisperProvider(s.local_whisper_model)
    if name == "mock":
        return MockProvider()
    raise errors.misconfigured(f"unknown transcription provider {name!r}")
