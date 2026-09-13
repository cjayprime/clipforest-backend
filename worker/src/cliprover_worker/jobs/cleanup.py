"""cleanup: storage purge for deleted videos, retention policy and self-healing
re-enqueues (PRD §16.3). All operations are idempotent."""

from __future__ import annotations

from datetime import datetime, timezone

from .. import keys
from ..log import get_logger
from ..queues import CLEANUP
from .base import JobContext, JobHandler

log = get_logger(__name__)


class CleanupHandler(JobHandler):
    queue = CLEANUP

    def entity_id(self, data: dict) -> str:
        return str(data.get("videoId") or data.get("kind"))

    async def run(self, ctx: JobContext) -> dict | None:
        kind = ctx.data.get("kind")
        if kind == "purge-video":
            return await self.purge_video(ctx.data["videoId"], ctx.data.get("userId"))
        if kind == "janitor":
            return await self.janitor(ctx)
        return {"skipped": f"unknown-kind-{kind}"}

    async def purge_video(self, video_id: str, user_id: str | None) -> dict:
        v = await self.db.get_video(video_id)
        if v is None:
            return {"skipped": "already-purged"}
        if not v["deleted_at"]:
            return {"skipped": "not-deleted"}
        prefix = keys.video_prefix(str(v["user_id"]), video_id)
        await self.svc.storage.abort_multipart_uploads(prefix)
        deleted = await self.svc.storage.delete_prefix(prefix)
        await self.db.update_video(video_id, purged_at=datetime.now(timezone.utc))
        await self.db.delete_video_row(video_id)
        log.info("Purged video storage and rows", extra={"objects": deleted})
        return {"objectsDeleted": deleted}

    async def janitor(self, ctx: JobContext) -> dict:
        s, db, storage, queues = self.s, self.db, self.svc.storage, self.svc.queues
        stats = {"requeuedVideos": 0, "requeuedRenders": 0, "purges": 0, "outputsDeleted": 0, "sourcesExpired": 0}

        # Self-heal: a QUEUED row whose enqueue was lost (e.g. Redis blip) gets its deterministic job re-added.
        for row in await db.stale_queued_videos():
            await queues.ingest(str(row["video_id"]), row["pipeline_version"], int(row["processing_run"]), None)
            stats["requeuedVideos"] += 1
        for row in await db.stale_queued_renders():
            await queues.render(str(row["render_id"]), str(row["video_id"]), int(row["attempt"]), None)
            stats["requeuedRenders"] += 1

        for row in await db.unpurged_deleted_videos():
            vid = str(row["video_id"])
            await queues.cleanup("purge-video", vid, {"videoId": vid, "userId": str(row["user_id"])})
            stats["purges"] += 1

        for row in await db.deleted_render_outputs():
            prefix = keys.render_prefix(str(row["user_id"]), str(row["video_id"]), str(row["render_id"]))
            await storage.delete_prefix(prefix)
            await db.update_render(str(row["render_id"]), output_key=None, thumbnail_key=None)
            stats["outputsDeleted"] += 1

        if s.render_retention_days > 0:
            for row in await db.expired_render_outputs(s.render_retention_days):
                await storage.delete_prefix(keys.render_prefix(str(row["user_id"]), str(row["video_id"]), str(row["render_id"])))
                await db.update_render(str(row["render_id"]), output_key=None, thumbnail_key=None)
                stats["outputsDeleted"] += 1

        if s.source_retention_days > 0:
            for row in await db.expired_sources(s.source_retention_days):
                await storage.delete_keys([row["object_key"], row["proxy_key"]])
                await db.update_video(str(row["video_id"]), source_expired_at=datetime.now(timezone.utc))
                stats["sourcesExpired"] += 1
        return stats
