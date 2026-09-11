"""transcription: provider call, normalization and persistence (PRD §6, progress 15-50%).

Idempotent: one transcript row per (video, version); a completed row is never
re-transcribed, and async providers resume from their persisted job ID.
"""

from __future__ import annotations

import contextlib
import time

from .. import errors, gates
from ..errors import PipelineError
from ..media.ffmpeg import extract_audio
from ..metrics import TRANSCRIPTION_AUDIO_MINUTES, TRANSCRIPTION_LATENCY
from ..queues import TRANSCRIPTION
from ..transcription.base import AudioInput, ProviderContext
from ..transcription.normalize import monotonic_ratio, normalize
from ..transcription.providers import get_provider, resolve_provider_name
from .base import JobContext, JobHandler


class TranscriptionHandler(JobHandler):
    queue = TRANSCRIPTION

    async def run(self, ctx: JobContext) -> dict | None:
        s, db, storage = self.s, self.db, self.svc.storage
        vid = ctx.data["videoId"]
        version = int(ctx.data["transcriptVersion"])
        v = await db.get_video(vid)
        if not v or v["deleted_at"]:
            return {"skipped": "deleted"}
        if v["status"] == "ANALYZING" and v["active_transcript_id"]:
            await self.svc.queues.analysis(vid, str(v["active_transcript_id"]), s.analysis_version, v["analysis_run"], ctx.correlation_id)
            return {"skipped": "already-transcribed"}
        if v["status"] != "TRANSCRIBING":
            return {"skipped": f"status-{v['status']}"}

        provider_name = resolve_provider_name(s)
        provider = get_provider(provider_name, s)
        row = await db.ensure_transcript(vid, version, provider_name)
        tid = str(row["id"])

        if row["status"] != "COMPLETED":
            if row["provider"] != provider_name:
                await db.update_transcript(tid, provider=provider_name, provider_job_id=None)
                row["provider_job_id"] = None
            await db.update_transcript(tid, status="PROCESSING")
            await self.video_progress(v, 16, stage="transcription", substage="preparing-audio", force=True)

            audio = ctx.workspace.file("audio.mp3")
            if v["audio_key"] and await storage.head(v["audio_key"]):
                await storage.download(v["audio_key"], audio)
            else:
                src = await self.source_input(v, ctx.workspace)
                await extract_audio(src, audio, duration_ms=v["duration_ms"])

            pctx = ProviderContext(
                provider_job_id=row.get("provider_job_id"),
                save_job_id=lambda job_id: db.update_transcript(tid, provider_job_id=job_id),
                on_progress=lambda f: self.video_progress(v, 18 + int(f * 30), substage="transcribing"),
                seed=vid,
            )
            await self.video_progress(v, 18, substage="transcribing", force=True)
            started = time.monotonic()
            gate = gates.heavy_cpu if provider.heavy_cpu else contextlib.nullcontext()
            async with gate:
                raw = await provider.transcribe(AudioInput(audio, int(v["duration_ms"]), s.transcription_language), pctx)
            TRANSCRIPTION_LATENCY.labels(provider_name).observe(time.monotonic() - started)
            TRANSCRIPTION_AUDIO_MINUTES.labels(provider_name).inc(v["duration_ms"] / 60000)

            result = normalize(raw, int(v["duration_ms"]), provider_name)
            if not result.words:
                await db.update_transcript(tid, status="FAILED", error_code="TRANSCRIPTION_NO_SPEECH")
                raise errors.transcription_no_speech()
            result.provider_metadata["monotonicRatio"] = round(monotonic_ratio(result.words), 5)
            await db.update_transcript(
                tid,
                status="COMPLETED",
                language=result.language,
                full_text=result.full_text,
                segments=[seg.to_json() for seg in result.segments],
                words=[w.to_json() for w in result.words],
                word_count=len(result.words),
                duration_ms=result.duration_ms,
                provider=provider_name,
                provider_metadata=result.provider_metadata,
                error_code=None,
            )
            await db.record_usage(
                str(v["user_id"]), "transcription.minutes", round(v["duration_ms"] / 60000, 3), "minutes",
                f"transcription:{vid}:v{version}", video_id=vid,
            )
            language = result.language
        else:
            language = row.get("language")

        # The transcript is persisted before global analysis begins (PRD §6.4).
        await db.update_video(vid, active_transcript_id=tid, language=language)
        if v["audio_key"] and not s.debug_retain_audio:
            await storage.delete_keys([v["audio_key"]])
            await db.update_video(vid, audio_key=None)

        next_run = int(v["analysis_run"]) + 1
        if await db.transition_video(vid, "ANALYZING", ("TRANSCRIBING",), progress=50, stage="analysis", substage="queued", analysis_run=next_run):
            v = await db.get_video(vid)
            await self.svc.events.video(v)
            await self.svc.queues.analysis(vid, tid, s.analysis_version, next_run, ctx.correlation_id)
        return {"transcriptId": tid, "provider": provider_name}

    async def on_failure(self, ctx: JobContext, err: PipelineError) -> None:
        await self.fail_video(ctx.data["videoId"], err, ctx.correlation_id)

    async def on_retry(self, ctx: JobContext, err: PipelineError) -> None:
        await self.mark_video_retrying(ctx.data["videoId"], err)
