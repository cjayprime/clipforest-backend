"""BullMQ producer side of the worker: stages chain by enqueueing the next stage.

Queue names, payloads and job-ID conventions are defined in
backend/contracts/queues.schema.json and shared with the NestJS API.
"""

from __future__ import annotations

from bullmq import Queue

from .keys import JobIds

INGEST = "video-ingest"
TRANSCRIPTION = "transcription"
ANALYSIS = "analysis"
RENDER = "render"
CLEANUP = "cleanup"

_KEEP = {"removeOnComplete": {"age": 24 * 3600, "count": 5000}, "removeOnFail": {"age": 14 * 24 * 3600}}

JOB_DEFAULTS: dict[str, dict] = {
    INGEST: {"attempts": 3, "backoff": {"type": "exponential", "delay": 10_000}, **_KEEP},
    TRANSCRIPTION: {"attempts": 5, "backoff": {"type": "exponential", "delay": 15_000}, **_KEEP},
    ANALYSIS: {"attempts": 3, "backoff": {"type": "exponential", "delay": 10_000}, **_KEEP},
    RENDER: {"attempts": 2, "backoff": {"type": "exponential", "delay": 30_000}, **_KEEP},
    CLEANUP: {"attempts": 5, "backoff": {"type": "exponential", "delay": 30_000}, **_KEEP},
}


class QueueProducer:
    def __init__(self, redis_url: str, prefix: str):
        self._redis_url = redis_url
        self._prefix = prefix
        self._queues: dict[str, Queue] = {}

    def _queue(self, name: str) -> Queue:
        if name not in self._queues:
            self._queues[name] = Queue(name, {"connection": self._redis_url, "prefix": self._prefix})
        return self._queues[name]

    async def add(self, queue: str, name: str, data: dict, job_id: str) -> None:
        opts = {**JOB_DEFAULTS[queue], "jobId": job_id}
        await self._queue(queue).add(name, data, opts)

    async def ingest(self, video_id: str, pipeline_version: str, processing_run: int, correlation_id: str | None) -> None:
        await self.add(
            INGEST,
            "ingest",
            {"videoId": video_id, "pipelineVersion": pipeline_version, "processingRun": processing_run, "correlationId": correlation_id},
            JobIds.ingest(video_id, pipeline_version, processing_run),
        )

    async def transcription(self, video_id: str, transcript_version: int, processing_run: int, correlation_id: str | None) -> None:
        await self.add(
            TRANSCRIPTION,
            "transcribe",
            {"videoId": video_id, "transcriptVersion": transcript_version, "processingRun": processing_run, "correlationId": correlation_id},
            JobIds.transcription(video_id, transcript_version, processing_run),
        )

    async def analysis(self, video_id: str, transcript_id: str, analysis_version: str, analysis_run: int, correlation_id: str | None) -> None:
        await self.add(
            ANALYSIS,
            "analyze",
            {
                "videoId": video_id,
                "transcriptId": transcript_id,
                "analysisVersion": analysis_version,
                "analysisRun": analysis_run,
                "correlationId": correlation_id,
            },
            JobIds.analysis(video_id, analysis_version, analysis_run),
        )

    async def render(self, render_id: str, video_id: str, attempt: int, correlation_id: str | None) -> None:
        await self.add(
            RENDER,
            "render",
            {"renderId": render_id, "videoId": video_id, "settingsVersion": 1, "attempt": attempt, "correlationId": correlation_id},
            JobIds.render(render_id, attempt),
        )

    async def cleanup(self, kind: str, entity_id: str, data: dict) -> None:
        await self.add(CLEANUP, kind, {"kind": kind, **data}, JobIds.cleanup(kind, entity_id))

    async def close(self) -> None:
        for q in self._queues.values():
            await q.close()
