"""Clip-local frame sampling: decodes only the candidate window at a low rate and
small size (PRD §9.4 — never the full source)."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import numpy as np

from ..media.ffmpeg import input_opts


async def sample_frames(
    src: str, start_ms: int, duration_ms: int, fps: float, display_w: int, display_h: int, width: int = 640
) -> AsyncIterator[tuple[int, np.ndarray]]:
    width = min(width, display_w) // 2 * 2
    height = max(2, int(round(width * display_h / display_w / 2)) * 2)
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-ss", f"{start_ms / 1000:.3f}", "-t", f"{duration_ms / 1000:.3f}",
        *input_opts(src), "-i", src,
        "-an", "-sn", "-vf", f"fps={fps},scale={width}:{height}",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    frame_bytes = width * height * 3
    index = 0
    try:
        assert proc.stdout
        while True:
            try:
                buf = await proc.stdout.readexactly(frame_bytes)
            except asyncio.IncompleteReadError:
                break
            yield int(round(index * 1000 / fps)), np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
            index += 1
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
