"""Prometheus metrics (PRD §19.2). Exposed on WORKER_METRICS_PORT."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

JOBS = Counter("cliprover_worker_jobs_total", "Jobs finished by queue and outcome", ["queue", "outcome"])
STAGE_DURATION = Histogram(
    "cliprover_worker_stage_duration_seconds",
    "Stage duration by queue",
    ["queue"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1200, 2400, 3600, 7200),
)
RENDER_SECONDS_PER_OUTPUT_MINUTE = Histogram(
    "cliprover_render_seconds_per_output_minute",
    "Wall-clock render seconds per minute of output video",
    buckets=(5, 10, 20, 30, 45, 60, 90, 120, 180, 300, 600),
)
TRANSCRIPTION_LATENCY = Histogram(
    "cliprover_transcription_latency_seconds",
    "Transcription provider latency",
    ["provider"],
    buckets=(5, 15, 30, 60, 120, 300, 600, 1200, 2400),
)
TRANSCRIPTION_AUDIO_MINUTES = Counter(
    "cliprover_transcription_audio_minutes_total", "Audio minutes sent to transcription (cost units)", ["provider"]
)
LLM_CALLS = Counter("cliprover_llm_calls_total", "LLM calls by purpose and outcome", ["purpose", "outcome"])
LLM_TOKENS = Counter("cliprover_llm_tokens_total", "LLM tokens by direction", ["direction"])
CANDIDATES = Counter("cliprover_candidates_created_total", "Candidates persisted by analyses")
ANALYSIS_EMPTY = Counter("cliprover_analysis_empty_total", "Analyses that produced zero candidates")
TEMP_DISK_HIGH_WATER = Gauge("cliprover_temp_disk_high_water_bytes", "Largest per-job working directory observed")
TEMP_DISK_FREE = Gauge("cliprover_temp_disk_free_bytes", "Free bytes on the working-directory volume")
FRAMING_STRATEGY = Counter("cliprover_render_framing_total", "Framing strategies used by renders", ["strategy"])


def serve(port: int) -> None:
    if port > 0:
        start_http_server(port)
