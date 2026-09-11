"""Typed environment configuration for the worker (mirrors .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

ALL_QUEUES = ("video-ingest", "transcription", "analysis", "render", "cleanup")


def _str(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return default if v is None or v == "" else v


def _int(name: str, default: int) -> int:
    v = os.environ.get(name)
    return default if not v else int(float(v))


def _float(name: str, default: float) -> float:
    v = os.environ.get(name)
    return default if not v else float(v)


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if not v else v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _str("DATABASE_URL", "postgresql://clipforest:clipforest@localhost:5432/clipforest"))
    redis_url: str = field(default_factory=lambda: _str("REDIS_URL", "redis://localhost:6379"))
    queue_prefix: str = field(default_factory=lambda: _str("QUEUE_PREFIX", "clipforest"))
    events_channel: str = field(default_factory=lambda: _str("EVENTS_CHANNEL", "clipforest:events"))
    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO"))
    metrics_port: int = field(default_factory=lambda: _int("WORKER_METRICS_PORT", 9100))

    # Storage (R2 / S3-compatible). The worker signs URLs against its own internal endpoint.
    s3_endpoint: str | None = field(default_factory=lambda: _str("S3_ENDPOINT"))
    s3_region: str = field(default_factory=lambda: _str("S3_REGION", "auto"))
    s3_bucket: str = field(default_factory=lambda: _str("S3_BUCKET", "clipforest"))
    s3_access_key_id: str | None = field(default_factory=lambda: _str("S3_ACCESS_KEY_ID"))
    s3_secret_access_key: str | None = field(default_factory=lambda: _str("S3_SECRET_ACCESS_KEY"))
    s3_force_path_style: bool = field(default_factory=lambda: _bool("S3_FORCE_PATH_STYLE", False))
    source_read_mode: str = field(default_factory=lambda: _str("SOURCE_READ_MODE", "url"))  # url | download

    # Which queues this process consumes (scale-out: run render-only workers on other hosts).
    worker_queues: tuple[str, ...] = field(
        default_factory=lambda: tuple(q.strip() for q in (_str("WORKER_QUEUES", ",".join(ALL_QUEUES)) or "").split(",") if q.strip())
    )
    concurrency_ingest: int = field(default_factory=lambda: _int("CONCURRENCY_INGEST", 1))
    concurrency_transcription: int = field(default_factory=lambda: _int("CONCURRENCY_TRANSCRIPTION", 3))
    concurrency_analysis: int = field(default_factory=lambda: _int("CONCURRENCY_ANALYSIS", 6))
    concurrency_render: int = field(default_factory=lambda: _int("CONCURRENCY_RENDER", 1))
    concurrency_cleanup: int = field(default_factory=lambda: _int("CONCURRENCY_CLEANUP", 1))

    # Local working directories and disk guard.
    tmp_root: str = field(default_factory=lambda: _str("WORKER_TMP_ROOT", "/tmp/video-jobs"))
    disk_min_free_bytes: int = field(default_factory=lambda: _int("DISK_MIN_FREE_BYTES", 5 * 1024**3))
    stale_workdir_hours: float = field(default_factory=lambda: _float("STALE_WORKDIR_HOURS", 6))
    janitor_interval_sec: int = field(default_factory=lambda: _int("JANITOR_INTERVAL_SEC", 600))

    # Media validation limits.
    max_video_duration_sec: int = field(default_factory=lambda: _int("MAX_VIDEO_DURATION_SEC", 3 * 3600))
    max_upload_bytes: int = field(default_factory=lambda: _int("MAX_UPLOAD_BYTES", 5 * 1024**3))

    pipeline_version: str = field(default_factory=lambda: _str("PIPELINE_VERSION", "2026-09-mvp1"))
    analysis_version: str = field(default_factory=lambda: _str("ANALYSIS_VERSION", "mvp1"))

    # Transcription providers.
    transcription_provider: str = field(default_factory=lambda: _str("TRANSCRIPTION_PROVIDER", "auto"))
    assemblyai_api_key: str | None = field(default_factory=lambda: _str("ASSEMBLYAI_API_KEY"))
    deepgram_api_key: str | None = field(default_factory=lambda: _str("DEEPGRAM_API_KEY"))
    deepgram_model: str = field(default_factory=lambda: _str("DEEPGRAM_MODEL", "nova-3"))
    whisper_api_key: str | None = field(default_factory=lambda: _str("WHISPER_API_KEY"))
    whisper_api_base: str = field(default_factory=lambda: _str("WHISPER_API_BASE", "https://api.openai.com/v1"))
    whisper_api_model: str = field(default_factory=lambda: _str("WHISPER_API_MODEL", "whisper-1"))
    local_whisper_model: str = field(default_factory=lambda: _str("LOCAL_WHISPER_MODEL", "small"))
    transcription_language: str | None = field(default_factory=lambda: _str("TRANSCRIPTION_LANGUAGE"))
    debug_retain_audio: bool = field(default_factory=lambda: _bool("DEBUG_RETAIN_AUDIO", False))

    # Highlight analysis.
    llm_provider: str = field(default_factory=lambda: _str("LLM_PROVIDER", "auto"))  # auto | anthropic | openai | heuristic
    llm_model: str = field(default_factory=lambda: _str("LLM_MODEL", "claude-opus-5"))
    openai_model: str = field(default_factory=lambda: _str("OPENAI_MODEL", "gpt-5.5"))
    openai_base_url: str | None = field(default_factory=lambda: _str("OPENAI_BASE_URL"))
    llm_effort: str | None = field(default_factory=lambda: _str("LLM_EFFORT"))  # low|medium|high|xhigh|max
    llm_max_concurrency: int = field(default_factory=lambda: _int("LLM_MAX_CONCURRENCY", 4))
    llm_refusal_fallbacks: bool = field(default_factory=lambda: _bool("LLM_REFUSAL_FALLBACKS", True))
    analysis_window_ms: int = field(default_factory=lambda: _int("ANALYSIS_WINDOW_MS", 5 * 60_000))
    analysis_overlap_ms: int = field(default_factory=lambda: _int("ANALYSIS_OVERLAP_MS", 60_000))
    candidate_min_ms: int = field(default_factory=lambda: _int("CANDIDATE_MIN_MS", 20_000))
    candidate_max_ms: int = field(default_factory=lambda: _int("CANDIDATE_MAX_MS", 90_000))
    candidates_per_window: int = field(default_factory=lambda: _int("CANDIDATES_PER_WINDOW", 5))
    global_shortlist: int = field(default_factory=lambda: _int("GLOBAL_SHORTLIST", 15))
    dedupe_iou: float = field(default_factory=lambda: _float("DEDUPE_IOU_THRESHOLD", 0.5))
    pre_roll_ms: int = field(default_factory=lambda: _int("PRE_ROLL_MS", 250))
    post_roll_ms: int = field(default_factory=lambda: _int("POST_ROLL_MS", 350))

    # Rendering.
    face_sample_fps: float = field(default_factory=lambda: _float("FACE_SAMPLE_FPS", 3.0))
    face_min_coverage: float = field(default_factory=lambda: _float("FACE_MIN_COVERAGE", 0.3))
    fallback_framing: str = field(default_factory=lambda: _str("FALLBACK_FRAMING", "center"))  # center | fit
    min_subject_dwell_ms: int = field(default_factory=lambda: _int("MIN_SUBJECT_DWELL_MS", 2500))
    face_model_path: str = field(default_factory=lambda: _str("FACE_MODEL_PATH", "/opt/models/face_detection_yunet_2023mar.onnx"))
    x264_preset: str = field(default_factory=lambda: _str("X264_PRESET", "veryfast"))
    x264_crf: int = field(default_factory=lambda: _int("X264_CRF", 20))
    ffmpeg_threads: int = field(default_factory=lambda: _int("FFMPEG_THREADS", 0))
    audio_normalize: bool = field(default_factory=lambda: _bool("AUDIO_NORMALIZE", False))
    render_timeout_sec: int = field(default_factory=lambda: _int("RENDER_TIMEOUT_SEC", 1800))
    fonts_dir: str = field(default_factory=lambda: _str("CAPTION_FONTS_DIR", "/usr/share/fonts"))

    # Retention (0 = keep forever).
    render_retention_days: int = field(default_factory=lambda: _int("RENDER_RETENTION_DAYS", 0))
    source_retention_days: int = field(default_factory=lambda: _int("SOURCE_RETENTION_DAYS", 0))

    def concurrency_for(self, queue: str) -> int:
        return {
            "video-ingest": self.concurrency_ingest,
            "transcription": self.concurrency_transcription,
            "analysis": self.concurrency_analysis,
            "render": self.concurrency_render,
            "cleanup": self.concurrency_cleanup,
        }[queue]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
