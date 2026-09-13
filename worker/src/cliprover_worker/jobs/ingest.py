"""video-ingest: fetch/verify source, ffprobe validation, poster, optional browser
proxy and speech-audio extraction (PRD §5, progress 0-15%)."""

from __future__ import annotations

from datetime import datetime, timezone

from .. import errors, keys
from ..errors import PipelineError
from ..ffmpeg import FfmpegError, extract_audio, extract_frame, make_proxy, probe
from ..queues import INGEST
from ..sources import download_youtube
from ..workspace import ensure_disk
from .base import JobContext, JobHandler


class IngestHandler(JobHandler):
    queue = INGEST

    async def next_transcript_version(self, video_id: str) -> int:
        latest = await self.db.latest_transcript_version(video_id)
        if latest == 0:
            return 1
        row = await self.db.fetchone("SELECT status FROM transcripts WHERE video_id = %s AND version = %s", [video_id, latest])
        return latest + 1 if row and row["status"] == "COMPLETED" else latest

    async def run(self, ctx: JobContext) -> dict | None:
        s, db, storage = self.s, self.db, self.svc.storage
        vid = ctx.data["videoId"]
        v = await db.get_video(vid)
        if not v or v["deleted_at"]:
            return {"skipped": "deleted"}
        if int(ctx.data.get("processingRun") or 0) < v["processing_run"]:
            return {"skipped": "stale-run"}
        if v["status"] == "TRANSCRIBING":
            # Crash between the transition and the enqueue: make sure the next stage exists (idempotent job id).
            await self.svc.queues.transcription(vid, await self.next_transcript_version(vid), v["processing_run"], ctx.correlation_id)
            return {"skipped": "already-ingested"}
        if v["status"] not in ("QUEUED", "INGESTING"):
            return {"skipped": f"status-{v['status']}"}
        if v["status"] == "QUEUED" and not await db.transition_video(vid, "INGESTING", ("QUEUED",), stage="ingest", substage="validating", progress=1):
            return {"skipped": "race"}
        v = await db.get_video(vid)
        await self.svc.events.video(v)

        ws = ctx.workspace
        ensure_disk(s.tmp_root, s.disk_min_free_bytes)
        user = str(v["user_id"])
        local_src = None

        if v["source_type"] == "url":
            await self.video_progress(v, 2, substage="downloading", force=True)
            path, info = await download_youtube(
                v["source_url"], ws.path, s, lambda f: self.video_progress(v, 2 + int(f * 6), substage="downloading")
            )
            size = path.stat().st_size
            if size > s.max_upload_bytes:
                raise errors.video_too_large(s.max_upload_bytes)
            key = keys.source_key(user, vid, f"youtube-{info['id']}{path.suffix or '.mp4'}")
            await self.video_progress(v, 8, substage="storing", force=True)
            await storage.upload(path, key, "video/mp4")
            fields = {
                "object_key": key,
                "size_bytes": size,
                "content_type": "video/mp4",
                "upload_completed_at": datetime.now(timezone.utc),
                "title": (info.get("title") or v["title"])[:200],
            }
            await db.update_video(vid, **fields)
            v.update(fields)
            local_src = str(path)
        else:
            head = await storage.head(v["object_key"])
            if head is None:
                raise errors.upload_missing()
            if int(head.get("ContentLength") or 0) > s.max_upload_bytes:
                raise errors.video_too_large(s.max_upload_bytes)

        src = local_src or await self.source_input(v, ws)
        await self.video_progress(v, 9, substage="probing", force=True)
        info = await probe(src)
        if info.duration_ms > s.max_video_duration_sec * 1000:
            raise errors.video_too_long(s.max_video_duration_sec)
        if not info.has_audio:
            raise errors.video_no_audio()

        # Decoding a real frame catches corrupt or undecodable media early, and gives the dashboard a poster.
        thumb = ws.file("thumb.jpg")
        try:
            await extract_frame(src, thumb, at_ms=min(info.duration_ms // 10, 30_000))
        except FfmpegError as exc:
            if "decoder" in exc.stderr_tail.lower() or "codec" in exc.stderr_tail.lower():
                raise errors.video_unsupported_codec(info.video_codec) from exc
            raise errors.video_unreadable(exc.stderr_tail) from exc
        if not thumb.exists() or thumb.stat().st_size == 0:
            raise errors.video_unreadable("no decodable frames")
        thumb_key = keys.thumbnail_key(user, vid)
        await storage.upload(thumb, thumb_key, "image/jpeg")
        meta = {
            "duration_ms": info.duration_ms,
            "width": info.display_width,
            "height": info.display_height,
            "fps": info.fps,
            "orientation": info.orientation,
            "has_audio": info.has_audio,
            "video_codec": info.video_codec,
            "audio_codec": info.audio_codec,
            "container": info.container[:60],
            "thumbnail_key": thumb_key,
        }
        if info.size_bytes:
            meta["size_bytes"] = info.size_bytes
        await db.update_video(vid, **meta)
        v.update(meta)

        # Charged here: the video is now known to be valid and measured, and the
        # expensive part — preview, audio extraction, transcription — hasn't started.
        await self.charge_video(v)

        if not info.browser_compatible:
            await self.video_progress(v, 10, substage="making-preview", force=True)
            proxy = ws.file("proxy.mp4")
            await make_proxy(
                src, proxy, duration_ms=info.duration_ms,
                on_progress=lambda f: self.video_progress(v, 10 + int(f * 3), substage="making-preview"),
                threads=s.ffmpeg_threads,
            )
            pkey = keys.proxy_key(user, vid)
            await storage.upload(proxy, pkey, "video/mp4")
            await db.update_video(vid, proxy_key=pkey)

        await self.video_progress(v, 13, substage="extracting-audio", force=True)
        audio = ws.file("audio.mp3")
        try:
            await extract_audio(src, audio, duration_ms=info.duration_ms)
        except FfmpegError as exc:
            raise errors.video_unreadable(exc.stderr_tail) from exc
        akey = keys.audio_key(user, vid)
        await storage.upload(audio, akey, "audio/mpeg")

        version = await self.next_transcript_version(vid)
        if await db.transition_video(vid, "TRANSCRIBING", ("INGESTING",), audio_key=akey, progress=15, stage="transcription", substage="queued"):
            v = await db.get_video(vid)
            await self.svc.events.video(v)
            await self.svc.queues.transcription(vid, version, v["processing_run"], ctx.correlation_id)
        return {"durationMs": info.duration_ms, "proxy": not info.browser_compatible, "transcriptVersion": version}

    async def on_failure(self, ctx: JobContext, err: PipelineError) -> None:
        await self.fail_video(ctx.data["videoId"], err, ctx.correlation_id)

    async def on_retry(self, ctx: JobContext, err: PipelineError) -> None:
        await self.mark_video_retrying(ctx.data["videoId"], err)
