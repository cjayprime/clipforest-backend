"""render: clip-local face analysis, crop path, captions, FFmpeg encode, R2 upload
(PRD §9-10). Render progress is separate from the video (0-100% per render)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from .. import errors, gates, keys
from ..captions.ass import build_ass, get_preset
from ..captions.grouping import clip_words, group_phrases
from ..errors import PipelineError
from ..media.ffmpeg import FfmpegError, extract_frame, probe, run_ffmpeg
from ..metrics import FRAMING_STRATEGY, RENDER_SECONDS_PER_OUTPUT_MINUTE
from ..queues import RENDER
from ..render.command import OUT_H, OUT_W, build_filtergraph, build_render_args, output_fps
from ..states import RENDER_ACTIVE
from ..transcription.base import Word
from ..vision.reframe import Sample, interpolate, plan_framing, sendcmd_script
from ..vision.sampling import sample_frames
from ..workspace import ensure_disk
from .base import JobContext, JobHandler, RenderFailureMixin


class RenderHandler(JobHandler, RenderFailureMixin):
    queue = RENDER
    entity_type = "render"

    def entity_id(self, data: dict) -> str:
        return str(data.get("renderId"))

    async def progress(self, r: dict, pct: int, status: str | None = None, substage: str | None = None, force: bool = False) -> None:
        now = time.monotonic()
        key = f"r:{r['id']}"
        if not force and status is None and now - self._last_progress.get(key, 0.0) < 1.5:
            return
        self._last_progress[key] = now
        fields: dict = {"progress": max(0, min(100, int(pct)))}
        if substage is not None:
            fields["substage"] = substage
        if status is not None:
            if not await self.db.transition_render(str(r["id"]), status, RENDER_ACTIVE, stage=status.lower(), **fields):
                raise errors.PipelineError("RENDER_CANCELLED", "The render is no longer active.", retryable=False)
            fields["status"] = status
            fields["stage"] = status.lower()
        else:
            await self.db.update_render(str(r["id"]), **fields)
        r.update(fields)
        await self.svc.events.render(r)

    async def run(self, ctx: JobContext) -> dict | None:
        s, db, storage = self.s, self.db, self.svc.storage
        rid = ctx.data["renderId"]
        r = await db.get_render(rid)
        if not r or r["deleted_at"]:
            return {"skipped": "deleted"}
        if r["status"] == "COMPLETED":
            return {"skipped": "completed"}
        if r["status"] not in RENDER_ACTIVE or int(ctx.data.get("attempt") or 1) < int(r["attempt"]):
            return {"skipped": f"status-{r['status']}"}
        v = await db.get_video(str(r["video_id"]))
        if not v or v["deleted_at"]:
            return {"skipped": "video-deleted"}

        started = time.monotonic()
        # Crash recovery re-enters PREPARING from whichever active state the previous attempt reached.
        await db.update_render(rid, status="PREPARING", stage="preparing", progress=2, substage="checking-source", started_at=datetime.now(timezone.utc))
        r = await db.get_render(rid)
        await self.svc.events.render(r)

        duration_ms = int(v["duration_ms"] or 0)
        start_ms, end_ms = int(r["start_ms"]), int(r["end_ms"])
        if not (0 <= start_ms < end_ms <= duration_ms):
            raise errors.render_invalid_range(f"{start_ms}-{end_ms} outside 0-{duration_ms}")
        clip_ms = end_ms - start_ms
        # Disk guard: output is ~1.5 MB/s at 1080x1920; keep generous headroom.
        ensure_disk(s.tmp_root, s.disk_min_free_bytes, needed_bytes=int(clip_ms / 1000 * 4 * 1024**2))

        settings = r["settings"] or {}
        framing_mode = settings.get("framingMode", "auto")
        captions = settings.get("captions") or {"enabled": True, "preset": "bold-default"}
        ws = ctx.workspace
        src = await self.source_input(v, ws)
        width, height = int(v["width"]), int(v["height"])

        async with gates.heavy_cpu:
            # 1) Face/subject analysis limited to the selected window.
            samples: list[Sample] = []
            if framing_mode == "auto":
                await self.progress(r, 5, status="ANALYZING_VISUALS", substage="detecting-faces")
                detector = self.svc.detector()
                expected = max(1, int(clip_ms / 1000 * s.face_sample_fps))
                async for t_ms, frame in sample_frames(src, start_ms, clip_ms, s.face_sample_fps, width, height):
                    boxes = await asyncio.to_thread(detector.detect, frame)
                    samples.append(Sample(t_ms, boxes))
                    await self.progress(r, 5 + int(min(1.0, len(samples) / expected) * 25))
            plan = plan_framing(
                framing_mode, samples, width, height,
                sample_fps=s.face_sample_fps, min_coverage=s.face_min_coverage,
                fallback=s.fallback_framing, dwell_ms=s.min_subject_dwell_ms,
            )
            FRAMING_STRATEGY.labels(plan.strategy).inc()

            fps = output_fps(v["fps"])
            sendcmd_file = None
            if plan.strategy == "auto" and len(plan.keyframes) > 1:
                frames = interpolate(plan.keyframes, clip_ms / 1000, float(fps))
                ws.file("crop.cmd").write_text(sendcmd_script(frames))
                sendcmd_file = "crop.cmd"

            # 2) Captions from persisted word timings (never re-transcribed).
            captions_file = None
            caption_stats: dict = {"enabled": bool(captions.get("enabled"))}
            if captions.get("enabled"):
                t = await db.get_transcript(str(v["active_transcript_id"])) if v["active_transcript_id"] else None
                words = [Word.from_json(w) for w in (t["words"] if t else [])]
                preset = get_preset(captions.get("preset", "bold-default"))
                cw = clip_words(words, start_ms, end_ms)
                phrases = group_phrases(cw, max_chars_per_line=preset.max_chars, max_words=preset.max_words, clip_length_ms=clip_ms)
                if phrases:
                    ws.file("captions.ass").write_text(build_ass(phrases, preset, OUT_W, OUT_H), encoding="utf-8")
                    captions_file = "captions.ass"
                caption_stats.update({"preset": preset.name, "phrases": len(phrases), "words": len(cw)})

            # 3) Single final encode.
            await self.progress(r, 32, status="RENDERING", substage="encoding")
            graph = build_filtergraph(plan, fps=fps, captions_file=captions_file, sendcmd_file=sendcmd_file, fonts_dir=s.fonts_dir)
            args = build_render_args(
                src=src, start_ms=start_ms, duration_ms=clip_ms, filtergraph=graph, has_audio=bool(v["has_audio"]),
                preset=s.x264_preset, crf=s.x264_crf, audio_normalize=s.audio_normalize,
            )
            try:
                await run_ffmpeg(
                    args, cwd=ws.path, timeout=s.render_timeout_sec, duration_ms=clip_ms, threads=s.ffmpeg_threads,
                    on_progress=lambda f: self.progress(r, 32 + int(f * 56), substage="encoding"),
                )
            except FfmpegError as exc:
                raise errors.render_ffmpeg_failed(exc.stderr_tail) from exc

        # 4) Verify the output matches the contract before publishing it.
        out = ws.file("output.mp4")
        info = await probe(str(out))
        if (info.display_width, info.display_height) != (OUT_W, OUT_H) or abs(info.duration_ms - clip_ms) > 600:
            raise errors.render_ffmpeg_failed(f"output verification failed: {info.display_width}x{info.display_height} {info.duration_ms}ms")
        thumb = ws.file("thumb.jpg")
        await extract_frame(str(out), thumb, at_ms=min(1000, clip_ms // 2), width=540)

        await self.progress(r, 90, status="UPLOADING", substage="uploading")
        user, vid = str(r["user_id"]), str(r["video_id"])
        out_key, thumb_key = keys.render_output_key(user, vid, rid), keys.render_thumb_key(user, vid, rid)
        await storage.upload(out, out_key, "video/mp4")
        await storage.upload(thumb, thumb_key, "image/jpeg")

        framing = {"strategy": plan.strategy, "requested": framing_mode, **plan.stats, "captions": caption_stats, "fps": fps}
        done = await db.transition_render(
            rid, "COMPLETED", ("UPLOADING",),
            progress=100, stage="completed", substage=None,
            output_key=out_key, thumbnail_key=thumb_key,
            width=info.display_width, height=info.display_height, duration_ms=info.duration_ms,
            file_size=out.stat().st_size, framing=framing, completed_at=datetime.now(timezone.utc),
        )
        if done:
            await db.record_usage(user, "render.seconds", round(info.duration_ms / 1000, 2), "seconds", f"render:{rid}", video_id=vid, render_id=rid)
            r = await db.get_render(rid)
            await self.svc.events.render(r)
        elapsed = time.monotonic() - started
        RENDER_SECONDS_PER_OUTPUT_MINUTE.observe(elapsed / max(clip_ms / 60000, 1 / 60))
        return {"strategy": plan.strategy, "durationMs": info.duration_ms, "elapsedSec": round(elapsed, 1)}

    async def on_failure(self, ctx: JobContext, err: PipelineError) -> None:
        if err.code != "RENDER_CANCELLED":
            await self.fail_render(ctx.data["renderId"], err, ctx.correlation_id)

    async def on_retry(self, ctx: JobContext, err: PipelineError) -> None:
        r = await self.db.get_render(ctx.data["renderId"])
        if r and r["status"] in RENDER_ACTIVE:
            await self.db.update_render(str(r["id"]), substage=f"retrying ({err.code})")
