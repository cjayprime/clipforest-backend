"""Normalized transcript schema (PRD §6.3) and the provider abstraction (PRD §6.1).

Provider-specific JSON never leaves an adapter: every provider returns a
TranscriptResult, which is what gets persisted and what analysis/captions read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol

import httpx

from .. import errors


@dataclass
class Word:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    speaker: str | None = None

    def to_json(self) -> dict:
        d: dict = {"startMs": self.start_ms, "endMs": self.end_ms, "text": self.text}
        if self.confidence is not None:
            d["confidence"] = round(self.confidence, 4)
        if self.speaker is not None:
            d["speaker"] = self.speaker
        return d

    @staticmethod
    def from_json(d: dict) -> "Word":
        return Word(int(d["startMs"]), int(d["endMs"]), str(d["text"]), d.get("confidence"), d.get("speaker"))


@dataclass
class Segment:
    id: str
    start_ms: int
    end_ms: int
    text: str
    speaker: str | None = None

    def to_json(self) -> dict:
        d: dict = {"id": self.id, "startMs": self.start_ms, "endMs": self.end_ms, "text": self.text}
        if self.speaker is not None:
            d["speaker"] = self.speaker
        return d

    @staticmethod
    def from_json(d: dict) -> "Segment":
        return Segment(str(d["id"]), int(d["startMs"]), int(d["endMs"]), str(d["text"]), d.get("speaker"))


@dataclass
class TranscriptResult:
    language: str | None
    duration_ms: int
    full_text: str
    segments: list[Segment]
    words: list[Word]
    provider: str
    provider_metadata: dict = field(default_factory=dict)

    @staticmethod
    def from_row(row: dict) -> "TranscriptResult":
        return TranscriptResult(
            language=row.get("language"),
            duration_ms=int(row.get("duration_ms") or 0),
            full_text=row.get("full_text") or "",
            segments=[Segment.from_json(s) for s in (row.get("segments") or [])],
            words=[Word.from_json(w) for w in (row.get("words") or [])],
            provider=row.get("provider") or "unknown",
            provider_metadata=row.get("provider_metadata") or {},
        )


@dataclass
class AudioInput:
    path: Path
    duration_ms: int
    language: str | None = None


@dataclass
class ProviderContext:
    """Lets async providers persist their job ID so a restarted worker resumes instead of resubmitting."""

    provider_job_id: str | None
    save_job_id: Callable[[str | None], Awaitable[None]]
    on_progress: Callable[[float], Awaitable[None]]
    seed: str = ""


@dataclass
class RawTranscript:
    """What an adapter hands to normalize(): provider data already mapped to Word/Segment."""

    words: list[Word]
    segments: list[Segment] | None
    language: str | None
    metadata: dict = field(default_factory=dict)


class TranscriptionProvider(Protocol):
    name: str
    heavy_cpu: bool

    async def transcribe(self, audio: AudioInput, ctx: ProviderContext) -> RawTranscript: ...


def raise_for_provider_status(resp: httpx.Response, provider: str) -> None:
    """Maps HTTP failures onto the retry policy (PRD §14.4)."""
    if resp.status_code < 400:
        return
    body = resp.text[:500]
    if resp.status_code == 429:
        retry_after = resp.headers.get("retry-after")
        raise errors.transcription_rate_limited(float(retry_after) if retry_after and retry_after.isdigit() else None)
    if resp.status_code in (401, 403):
        raise errors.misconfigured(f"{provider} rejected the credentials ({resp.status_code})")
    if resp.status_code >= 500 or resp.status_code in (408, 409):
        raise errors.transcription_failed(f"{provider} {resp.status_code}: {body}", retryable=True)
    raise errors.transcription_failed(f"{provider} {resp.status_code}: {body}", retryable=False)
