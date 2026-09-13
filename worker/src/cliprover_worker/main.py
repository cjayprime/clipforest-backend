"""Worker entrypoint: one BullMQ Worker per configured queue, Prometheus metrics,
and a periodic janitor tick. Scale out by running more processes/hosts with
WORKER_QUEUES set (e.g. render-only boxes)."""

from __future__ import annotations

import asyncio
import signal
import time

from bullmq import Worker

from . import log as logging_setup
from .config import get_settings
from .db import Database
from .events import EventPublisher
from .jobs.analysis import AnalysisHandler
from .jobs.base import Services, make_processor
from .jobs.cleanup import CleanupHandler
from .jobs.ingest import IngestHandler
from .jobs.render import RenderHandler
from .jobs.transcription import TranscriptionHandler
from .log import get_logger
from .metrics import serve
from .queues import QueueProducer
from .storage import Storage
from .analysis.llm import resolve_llm_name
from .transcription.providers import resolve_provider_name
from .workspace import sweep_stale

log = get_logger("cliprover_worker")

HANDLERS = {
    "video-ingest": IngestHandler,
    "transcription": TranscriptionHandler,
    "analysis": AnalysisHandler,
    "render": RenderHandler,
    "cleanup": CleanupHandler,
}

# Long media jobs keep their lock renewed every lockDuration/2; stalled jobs are reclaimed.
LOCK_DURATION_MS = {"video-ingest": 120_000, "transcription": 120_000, "analysis": 120_000, "render": 180_000, "cleanup": 60_000}


async def janitor_loop(svc: Services, stop: asyncio.Event) -> None:
    s = svc.settings
    while not stop.is_set():
        try:
            sweep_stale(s.tmp_root, s.stale_workdir_hours)
            bucket = int(time.time() // max(60, s.janitor_interval_sec))
            # Deterministic id: many worker processes, one janitor run per interval.
            await svc.queues.cleanup("janitor", f"t{bucket}", {})
        except Exception:  # noqa: BLE001
            log.exception("Janitor tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(60, s.janitor_interval_sec))
        except asyncio.TimeoutError:
            pass


async def main() -> None:
    s = get_settings()
    logging_setup.configure(s.log_level)
    serve(s.metrics_port)

    db = Database(s.database_url)
    await db.open()
    svc = Services(
        settings=s,
        db=db,
        storage=Storage(s),
        events=EventPublisher(s.redis_url, s.events_channel),
        queues=QueueProducer(s.redis_url, s.queue_prefix),
    )
    log.info(
        "Worker starting",
        extra={
            "queues": list(s.worker_queues),
            "transcriptionProvider": resolve_provider_name(s),
            "llmProvider": resolve_llm_name(s),
            "pipelineVersion": s.pipeline_version,
            "sourceReadMode": s.source_read_mode,
        },
    )

    workers: list[Worker] = []
    for queue in s.worker_queues:
        if queue not in HANDLERS:
            log.warning("Ignoring unknown queue", extra={"queue": queue})
            continue
        handler = HANDLERS[queue](svc)
        workers.append(
            Worker(
                queue,
                make_processor(handler, svc),
                {
                    "connection": s.redis_url,
                    "prefix": s.queue_prefix,
                    "concurrency": s.concurrency_for(queue),
                    "lockDuration": LOCK_DURATION_MS[queue],
                    "stalledInterval": 30_000,
                    "maxStalledCount": 2,
                },
            )
        )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows dev shells
            pass

    janitor = asyncio.create_task(janitor_loop(svc, stop))
    await stop.wait()
    log.info("Shutting down: finishing active jobs")
    for w in workers:
        await w.close()
    janitor.cancel()
    await svc.queues.close()
    await svc.events.close()
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
