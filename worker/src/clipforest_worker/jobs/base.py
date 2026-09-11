"""Common job wrapper: correlation logging, job_runs mirror, metrics, isolated
workspace, retry semantics and terminal-failure bookkeeping (PRD §14, §19)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bullmq import Job
from bullmq.custom_errors import UnrecoverableError

from .. import errors
from ..config import Settings
from ..db import Database
from ..errors import PipelineError
from ..events import EventPublisher
from ..log import bind, get_logger
from ..metrics import JOBS, STAGE_DURATION
from ..queues import QueueProducer
from ..states import RENDER_ACTIVE, VIDEO_PROCESSING
from ..storage import Storage
from ..workspace import Workspace

log = get_logger(__name__)


@dataclass
class Services:
    settings: Settings
    db: Database
    storage: Storage
    events: EventPublisher
    queues: QueueProducer
    _llm: Any = None
    _detector: Any = None

    def llm(self):
        if self._llm is None:
            from ..analysis.llm import resolve_llm

            self._llm = resolve_llm(self.settings)
            log.info("LLM provider selected", extra={"provider": self._llm.name, "model": self._llm.model})
        return self._llm

    def detector(self):
        if self._detector is None:
            from ..vision.detector import FaceDetector

            self._detector = FaceDetector(self.settings.face_model_path)
            log.info("Face detector ready", extra={"kind": self._detector.kind})
        return self._detector


@dataclass
class JobContext:
    job: Job
    data: dict
    attempt: int
    max_attempts: int
    workspace: Workspace
    correlation_id: str | None
    extra: dict = field(default_factory=dict)

    @property
    def is_final_attempt(self) -> bool:
        return self.attempt >= self.max_attempts


class JobHandler:
    queue: str = ""
    entity_type: str = "video"

    def __init__(self, svc: Services):
        self.svc = svc
        self.s = svc.settings
        self.db = svc.db
        self._last_progress: dict[str, float] = {}

    def entity_id(self, data: dict) -> str:
        return str(data.get("videoId"))

    async def run(self, ctx: JobContext) -> dict | None:  # pragma: no cover - abstract
        raise NotImplementedError

    async def on_failure(self, ctx: JobContext, err: PipelineError) -> None:
        """Terminal failure: persist a stable error on the entity."""

    async def on_retry(self, ctx: JobContext, err: PipelineError) -> None:
        """A retry will follow: surface that without failing the entity."""

    # ------------------------------------------------------- video helpers
    async def video_progress(self, video: dict, progress: int, *, stage: str | None = None, substage: str | None = None, force: bool = False) -> None:
        now = time.monotonic()
        key = str(video["id"])
        if not force and now - self._last_progress.get(key, 0.0) < 1.5:
            return
        self._last_progress[key] = now
        fields: dict[str, Any] = {"progress": max(0, min(100, int(progress)))}
        if stage is not None:
            fields["stage"] = stage
        if substage is not None:
            fields["substage"] = substage
        await self.db.update_video(key, **fields)
        video.update(fields)
        await self.svc.events.video(video)

    async def fail_video(self, video_id: str, err: PipelineError, correlation_id: str | None) -> None:
        v = await self.db.get_video(video_id)
        if not v or v["deleted_at"] or v["status"] not in VIDEO_PROCESSING:
            return
        await self.db.transition_video(
            video_id,
            "FAILED",
            VIDEO_PROCESSING,
            error_code=err.code,
            error_message=err.message,
            error_retryable=err.retryable,
            error_correlation_id=correlation_id,
            failed_stage=v["status"],
            substage=None,
        )
        v = await self.db.get_video(video_id)
        if v:
            await self.svc.events.video(v)

    async def mark_video_retrying(self, video_id: str, err: PipelineError) -> None:
        v = await self.db.get_video(video_id)
        if v and v["status"] in VIDEO_PROCESSING:
            await self.db.update_video(video_id, substage=f"retrying ({err.code})")
            v["substage"] = f"retrying ({err.code})"
            await self.svc.events.video(v)

    async def source_input(self, video: dict, ws: Workspace) -> str:
        """FFmpeg input for the source: a presigned URL (range reads, no full download) or a local copy."""
        key = video["object_key"]
        if not key:
            raise errors.render_source_missing()
        if self.s.source_read_mode == "download":
            ext = Path(key).suffix or ".mp4"
            path = ws.file(f"source{ext}")
            if not path.exists():
                await self.svc.storage.download(key, path)
            return str(path)
        return self.svc.storage.presign_get(key)


class RenderFailureMixin:
    db: Database
    svc: Services

    async def fail_render(self, render_id: str, err: PipelineError, correlation_id: str | None) -> None:
        r = await self.db.get_render(render_id)
        if not r or r["deleted_at"] or r["status"] not in RENDER_ACTIVE:
            return
        await self.db.transition_render(
            render_id,
            "FAILED",
            RENDER_ACTIVE,
            error_code=err.code,
            error_message=err.message,
            error_retryable=err.retryable,
            error_correlation_id=correlation_id,
            substage=None,
        )
        r = await self.db.get_render(render_id)
        if r:
            await self.svc.events.render(r)


def make_processor(handler: JobHandler, svc: Services):
    queue = handler.queue

    async def process(job: Job, token: str):
        data = job.data or {}
        attempt = int(job.attemptsMade) + 1
        max_attempts = int((job.opts or {}).get("attempts") or 1)
        entity_id = handler.entity_id(data)
        with bind(
            queueName=queue,
            jobId=job.id,
            attempt=attempt,
            correlationId=data.get("correlationId"),
            videoId=data.get("videoId"),
            renderId=data.get("renderId"),
            pipelineVersion=svc.settings.pipeline_version,
        ):
            started = time.monotonic()
            await svc.db.job_started(queue, str(job.id), handler.entity_type, entity_id, attempt, {"name": job.name})
            log.info("Job started")
            ws = Workspace(svc.settings.tmp_root, f"{queue}-{job.id}-a{attempt}")
            ctx = JobContext(job, data, attempt, max_attempts, ws, data.get("correlationId"))
            err: PipelineError
            try:
                with ws:
                    result = await handler.run(ctx)
                elapsed = time.monotonic() - started
                STAGE_DURATION.labels(queue).observe(elapsed)
                JOBS.labels(queue, "completed").inc()
                await svc.db.job_finished(queue, str(job.id), attempt, "completed", int(elapsed * 1000))
                log.info("Job completed", extra={"durationMs": int(elapsed * 1000), "result": result})
                return result
            except PipelineError as exc:
                err = exc
            except Exception as exc:  # noqa: BLE001 - classify unknown failures as retryable internal errors
                log.exception("Unhandled job error")
                err = errors.internal(repr(exc))

            final = (not err.retryable) or attempt >= max_attempts
            elapsed = time.monotonic() - started
            await svc.db.job_finished(queue, str(job.id), attempt, "failed" if final else "retrying", int(elapsed * 1000), err.code, err.message)
            JOBS.labels(queue, "failed" if final else "retried").inc()
            log.warning(
                "Job failed" if final else "Job attempt failed; will retry",
                extra={"code": err.code, "retryable": err.retryable, "details": err.details},
            )
            try:
                if final:
                    await handler.on_failure(ctx, err)
                else:
                    await handler.on_retry(ctx, err)
            except Exception:  # noqa: BLE001
                log.exception("Failure bookkeeping failed")
            if not err.retryable:
                raise UnrecoverableError(f"{err.code}: {err.message}")
            raise RuntimeError(f"{err.code}: {err.message}")

    return process
