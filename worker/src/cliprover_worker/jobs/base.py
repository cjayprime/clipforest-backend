"""What every queue handler shares: job context, long-lived services, the CPU gate, the handler base class and the BullMQ processor wrapper (PRD §14, §19)."""

from __future__ import annotations
from dataclasses import dataclass, field
from bullmq import Job
from typing import Any
import asyncio
import time
from pathlib import Path
from bullmq.custom_errors import UnrecoverableError
from ..workspace import Workspace
from ..config import Settings
from ..db import Database
from ..events import EventPublisher
from ..log import bind, get_logger
from ..queues import QueueProducer
from ..storage import Storage
from .. import errors
from ..credits import processing_cost
from ..errors import PipelineError
from ..states import VIDEO_PROCESSING
from ..metrics import JOBS, STAGE_DURATION


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
        """Resolved on first use: constructing a provider validates credentials."""
        if self._llm is None:
            from ..analysis.llm import resolve_llm

            self._llm = resolve_llm(self.settings)
            log.info("LLM provider selected", extra={"provider": self._llm.name, "model": self._llm.model})
        return self._llm

    def detector(self):
        """Loaded lazily: only render jobs with automatic framing need OpenCV models."""
        if self._detector is None:
            from ..vision import FaceDetector

            self._detector = FaceDetector(self.settings.face_model_path)
            log.info("Face detector ready", extra={"kind": self._detector.kind})
        return self._detector


heavy_cpu = asyncio.Semaphore(1)


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

    # ----------------------------------------------------- credit helpers
    async def charge(self, user_id: str, subject: str, amount: int, metadata: dict) -> None:
        """Debits the work about to start, or raises when the balance can't cover it.

        Idempotent per run: a retried job finds the open charge and carries on.
        A no-op unless CREDITS_ENFORCED.
        """
        if not self.s.credits_enforced or amount <= 0:
            return
        result = await self.db.charge_credits(user_id, subject, amount, metadata)
        if result.outcome == "insufficient":
            raise errors.insufficient_credits(amount, result.available)
        if result.outcome == "charged":
            await self.svc.events.credits(user_id, "SPENT")

    async def charge_video(self, video: dict) -> None:
        """Processing is charged per started minute, once the probe has measured it."""
        duration_ms = int(video["duration_ms"] or 0)
        await self.charge(
            str(video["user_id"]),
            f"video:{video['video_id']}",
            processing_cost(duration_ms, self.s.credit_cost_per_source_minute),
            {"videoId": str(video["video_id"]), "durationMs": duration_ms},
        )

    async def refund(self, user_id: str, subject: str, reason: str) -> None:
        """Returns the subject's open charge. Runs even when charging is switched
        off, so turning enforcement off never strands credits already taken."""
        if await self.db.refund_credits(user_id, subject, reason):
            await self.svc.events.credits(user_id, "REFUNDED")

    # ------------------------------------------------------- video helpers
    async def video_progress(
        self, video: dict, progress: int, *, stage: str | None = None, substage: str | None = None, force: bool = False
    ) -> None:
        now = time.monotonic()
        key = str(video["video_id"])
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
        failed = await self.db.transition_video(
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
        # Refund only a video that never became READY. One that did has already
        # delivered its transcript and moments; a failed *re-analysis* keeps its
        # charge, and retrying that analysis is free for the same reason.
        if failed and v["ready_at"] is None:
            await self.refund(str(v["user_id"]), f"video:{video_id}", err.code)
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


log = get_logger(__name__)


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

            final = (not err.auto_retry) or attempt >= max_attempts
            elapsed = time.monotonic() - started
            await svc.db.job_finished(
                queue, str(job.id), attempt, "failed" if final else "retrying", int(elapsed * 1000), err.code, err.message
            )
            JOBS.labels(queue, "failed" if final else "retried").inc()
            log.warning(
                "Job failed" if final else "Job attempt failed; will retry",
                extra={"code": err.code, "retryable": err.retryable, "autoRetry": err.auto_retry, "details": err.details},
            )
            try:
                if final:
                    await handler.on_failure(ctx, err)
                else:
                    await handler.on_retry(ctx, err)
            except Exception:  # noqa: BLE001
                log.exception("Failure bookkeeping failed")
            if not err.auto_retry:
                raise UnrecoverableError(f"{err.code}: {err.message}")
            raise RuntimeError(f"{err.code}: {err.message}")

    return process
