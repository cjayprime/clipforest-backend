"""Reference state machines (PRD §29); mirrors api/src/common/state-machine.ts."""

from __future__ import annotations

VIDEO_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "CREATED": ("UPLOADING", "QUEUED", "FAILED"),
    "UPLOADING": ("UPLOADING", "QUEUED", "FAILED"),
    "QUEUED": ("INGESTING", "FAILED"),
    "INGESTING": ("TRANSCRIBING", "FAILED"),
    "TRANSCRIBING": ("ANALYZING", "FAILED"),
    "ANALYZING": ("READY", "FAILED"),
    "READY": ("ANALYZING",),
    "FAILED": ("QUEUED", "TRANSCRIBING", "ANALYZING"),
}

RENDER_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "QUEUED": ("PREPARING", "FAILED"),
    "PREPARING": ("ANALYZING_VISUALS", "RENDERING", "FAILED"),
    "ANALYZING_VISUALS": ("RENDERING", "FAILED"),
    "RENDERING": ("UPLOADING", "FAILED"),
    "UPLOADING": ("COMPLETED", "FAILED"),
    "COMPLETED": (),
    "FAILED": ("QUEUED",),
}

VIDEO_PROCESSING = ("QUEUED", "INGESTING", "TRANSCRIBING", "ANALYZING")
RENDER_ACTIVE = ("QUEUED", "PREPARING", "ANALYZING_VISUALS", "RENDERING", "UPLOADING")


def can_transition_video(src: str, dst: str) -> bool:
    return dst in VIDEO_TRANSITIONS.get(src, ())


def can_transition_render(src: str, dst: str) -> bool:
    return dst in RENDER_TRANSITIONS.get(src, ())


def video_sources_for(dst: str) -> tuple[str, ...]:
    return tuple(s for s, targets in VIDEO_TRANSITIONS.items() if dst in targets)


def render_sources_for(dst: str) -> tuple[str, ...]:
    return tuple(s for s, targets in RENDER_TRANSITIONS.items() if dst in targets)
