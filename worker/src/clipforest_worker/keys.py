"""Deterministic R2 object layout (PRD §16.1) and queue job IDs (PRD §14.3).

Mirrors api/src/common/object-keys.ts and idempotency.ts.
"""

from __future__ import annotations


def video_prefix(user_id: str, video_id: str) -> str:
    return f"users/{user_id}/videos/{video_id}/"


def source_key(user_id: str, video_id: str, filename: str) -> str:
    return f"{video_prefix(user_id, video_id)}source/{filename}"


def audio_key(user_id: str, video_id: str, ext: str = "mp3") -> str:
    return f"{video_prefix(user_id, video_id)}derived/audio.{ext}"


def thumbnail_key(user_id: str, video_id: str) -> str:
    return f"{video_prefix(user_id, video_id)}derived/thumb.jpg"


def proxy_key(user_id: str, video_id: str) -> str:
    return f"{video_prefix(user_id, video_id)}derived/proxy.mp4"


def render_output_key(user_id: str, video_id: str, render_id: str) -> str:
    return f"{video_prefix(user_id, video_id)}renders/{render_id}/final.mp4"


def render_thumb_key(user_id: str, video_id: str, render_id: str) -> str:
    return f"{video_prefix(user_id, video_id)}renders/{render_id}/thumb.jpg"


def render_prefix(user_id: str, video_id: str, render_id: str) -> str:
    return f"{video_prefix(user_id, video_id)}renders/{render_id}/"


def is_owned_key(key: str | None, user_id: str, video_id: str) -> bool:
    if not key or ".." in key or "//" in key:
        return False
    return key.startswith(video_prefix(user_id, video_id))


class JobIds:
    """BullMQ forbids ':' in custom job IDs, so '.' separates the parts."""

    @staticmethod
    def ingest(video_id: str, pipeline_version: str, processing_run: int) -> str:
        return f"ingest.{video_id}.{pipeline_version}.r{processing_run}"

    @staticmethod
    def transcription(video_id: str, transcript_version: int, processing_run: int) -> str:
        return f"transcribe.{video_id}.v{transcript_version}.r{processing_run}"

    @staticmethod
    def analysis(video_id: str, analysis_version: str, analysis_run: int) -> str:
        return f"analyze.{video_id}.{analysis_version}.a{analysis_run}"

    @staticmethod
    def render(render_id: str, attempt: int) -> str:
        return f"render.{render_id}.a{attempt}"

    @staticmethod
    def cleanup(kind: str, entity_id: str) -> str:
        return f"cleanup.{kind}.{entity_id}"
