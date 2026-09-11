"""Pipeline error taxonomy (PRD §19.3) with stable codes and retry semantics (PRD §14.4)."""

from __future__ import annotations


class PipelineError(Exception):
    """An error with a stable machine code, a human-readable message and a retry policy."""

    def __init__(self, code: str, message: str, *, retryable: bool, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def __repr__(self) -> str:  # pragma: no cover
        return f"PipelineError({self.code!r}, retryable={self.retryable})"


def video_unreadable(detail: str = "") -> PipelineError:
    return PipelineError(
        "VIDEO_UNREADABLE",
        "This video could not be read. The file may be corrupt or in a format we can't decode.",
        retryable=False,
        details={"detail": detail[-500:]},
    )


def video_unsupported_codec(codec: str) -> PipelineError:
    return PipelineError("VIDEO_UNSUPPORTED_CODEC", f"This video format could not be decoded ({codec}).", retryable=False)


def video_no_video_stream() -> PipelineError:
    return PipelineError("VIDEO_NO_VIDEO_STREAM", "This file does not contain a video track.", retryable=False)


def video_no_audio() -> PipelineError:
    return PipelineError(
        "VIDEO_NO_AUDIO",
        "This video has no audio track. Finding moments requires speech, so it can't be analyzed.",
        retryable=False,
    )


def video_too_long(limit_sec: int) -> PipelineError:
    return PipelineError(
        "VIDEO_TOO_LONG", f"This video is longer than the {limit_sec // 60}-minute limit.", retryable=False
    )


def video_too_large(limit_bytes: int) -> PipelineError:
    return PipelineError(
        "VIDEO_TOO_LARGE", f"This video is larger than the {limit_bytes / 1024**3:.0f} GB limit.", retryable=False
    )


def source_unavailable(detail: str = "") -> PipelineError:
    return PipelineError(
        "VIDEO_SOURCE_UNAVAILABLE",
        "That video can't be imported. It may be private, age-restricted, region-locked or removed.",
        retryable=False,
        details={"detail": detail[-500:]},
    )


def source_unsupported(detail: str = "") -> PipelineError:
    return PipelineError("VIDEO_SOURCE_UNSUPPORTED", "This source is not supported.", retryable=False, details={"detail": detail})


def video_deleted() -> PipelineError:
    return PipelineError("VIDEO_DELETED", "The video was deleted.", retryable=False)


def upload_missing() -> PipelineError:
    return PipelineError("UPLOAD_OBJECT_MISSING", "The uploaded file could not be found in storage.", retryable=False)


def transcription_failed(detail: str, retryable: bool = True) -> PipelineError:
    return PipelineError(
        "TRANSCRIPTION_PROVIDER_ERROR",
        "The transcription service failed. We'll retry automatically.",
        retryable=retryable,
        details={"detail": detail[-500:]},
    )


def transcription_rate_limited(retry_after: float | None = None) -> PipelineError:
    return PipelineError(
        "TRANSCRIPTION_RATE_LIMITED",
        "The transcription service is busy. We'll retry shortly.",
        retryable=True,
        details={"retryAfter": retry_after},
    )


def transcription_no_speech() -> PipelineError:
    return PipelineError(
        "TRANSCRIPTION_NO_SPEECH", "No speech was detected in this video, so there is nothing to clip.", retryable=False
    )


def analysis_failed(detail: str, retryable: bool = True) -> PipelineError:
    return PipelineError(
        "ANALYSIS_LLM_ERROR", "Finding moments failed. We'll retry automatically.", retryable=retryable, details={"detail": detail[-500:]}
    )


def analysis_invalid_output(detail: str) -> PipelineError:
    return PipelineError(
        "ANALYSIS_INVALID_OUTPUT", "The analysis returned an invalid result. We'll retry.", retryable=True, details={"detail": detail[-500:]}
    )


def render_ffmpeg_failed(detail: str) -> PipelineError:
    return PipelineError("RENDER_FFMPEG_FAILED", "Encoding the clip failed.", retryable=True, details={"stderr": detail[-2000:]})


def render_invalid_range(detail: str) -> PipelineError:
    return PipelineError("RENDER_INVALID_RANGE", f"The clip range is invalid: {detail}", retryable=False)


def render_source_missing() -> PipelineError:
    return PipelineError("RENDER_SOURCE_MISSING", "The source video is no longer available.", retryable=False)


def storage_failed(detail: str) -> PipelineError:
    return PipelineError("STORAGE_TRANSFER_FAILED", "A storage transfer failed. We'll retry.", retryable=True, details={"detail": detail[-500:]})


def low_disk(free: int, needed: int) -> PipelineError:
    return PipelineError(
        "SYSTEM_LOW_DISK",
        "The server is low on disk space; the job was deferred. Please retry in a few minutes.",
        retryable=True,
        details={"freeBytes": free, "neededBytes": needed},
    )


def misconfigured(detail: str) -> PipelineError:
    return PipelineError("SYSTEM_MISCONFIGURED", "The processing service is misconfigured.", retryable=False, details={"detail": detail})


def internal(detail: str) -> PipelineError:
    return PipelineError("SYSTEM_INTERNAL", "An unexpected error occurred. We'll retry.", retryable=True, details={"detail": detail[-1000:]})
