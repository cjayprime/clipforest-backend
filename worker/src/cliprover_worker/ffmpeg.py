"""FFmpeg/ffprobe helpers: probing, frame and audio extraction, proxies, and a progress-reporting runner."""

from __future__ import annotations

import asyncio
import collections
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from . import errors


HTTP_INPUT_OPTS = [
    "-reconnect", "1",
    "-reconnect_streamed", "1",
    "-reconnect_on_network_error", "1",
    "-reconnect_delay_max", "5",
    "-rw_timeout", "30000000",
]

ProgressCb = Callable[[float], Awaitable[None]]


def input_opts(src: str) -> list[str]:
    return list(HTTP_INPUT_OPTS) if src.startswith(("http://", "https://")) else []


class FfmpegError(Exception):
    def __init__(self, returncode: int, stderr_tail: str):
        super().__init__(f"ffmpeg exited with {returncode}: {stderr_tail[-400:]}")
        self.returncode = returncode
        self.stderr_tail = stderr_tail


async def run_ffmpeg(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
    duration_ms: int | None = None,
    on_progress: ProgressCb | None = None,
    threads: int = 0,
) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-y"]
    if on_progress:
        cmd += ["-progress", "pipe:1"]
    if threads > 0:
        cmd += ["-threads", str(threads)]
    cmd += args
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(cwd) if cwd else None
    )
    tail: collections.deque[str] = collections.deque(maxlen=60)

    async def read_stdout() -> None:
        assert proc.stdout
        last = 0.0
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if on_progress and duration_ms and line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                frac = max(0.0, min(1.0, us / 1000 / duration_ms))
                now = time.monotonic()
                if now - last >= 1.0 or frac >= 1.0:
                    last = now
                    await on_progress(frac)

    async def read_stderr() -> None:
        assert proc.stderr
        async for raw in proc.stderr:
            tail.append(raw.decode(errors="replace").rstrip())

    try:
        await asyncio.wait_for(asyncio.gather(read_stdout(), read_stderr(), proc.wait()), timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise FfmpegError(-1, "timed out\n" + "\n".join(tail)) from exc
    except asyncio.CancelledError:
        proc.kill()
        raise
    if proc.returncode != 0:
        raise FfmpegError(proc.returncode or -1, "\n".join(tail))


# ------------------------------------------------------------------ ffprobe

BROWSER_VIDEO = {"h264", "vp8", "vp9", "av1"}
BROWSER_AUDIO = {"aac", "mp3", "opus", "vorbis", None}


@dataclass
class MediaInfo:
    duration_ms: int
    width: int
    height: int
    fps: float
    fps_str: str
    rotation: int
    has_audio: bool
    video_codec: str
    audio_codec: str | None
    container: str
    size_bytes: int | None
    pix_fmt: str | None

    @property
    def display_width(self) -> int:
        return self.height if self.rotation in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation in (90, 270) else self.height

    @property
    def orientation(self) -> str:
        w, h = self.display_width, self.display_height
        return "landscape" if w > h else "portrait" if h > w else "square"

    @property
    def browser_compatible(self) -> bool:
        c = self.container
        mp4ish = "mp4" in c or "mov" in c
        webm = "webm" in c or "matroska" in c
        if mp4ish:
            return self.video_codec in {"h264", "av1"} and self.audio_codec in BROWSER_AUDIO and self.pix_fmt in (None, "yuv420p", "yuvj420p")
        if webm and "webm" in c:
            return self.video_codec in {"vp8", "vp9", "av1"} and self.audio_codec in {"opus", "vorbis", None}
        return False


def _fps(stream: dict) -> tuple[float, str]:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(key) or ""
        if "/" in raw:
            n, d = raw.split("/", 1)
            try:
                if float(d) > 0 and float(n) > 0:
                    return float(n) / float(d), raw
            except ValueError:
                continue
    return 30.0, "30/1"


def _rotation(stream: dict) -> int:
    rot = 0
    tag = (stream.get("tags") or {}).get("rotate")
    if tag:
        try:
            rot = int(tag)
        except ValueError:
            rot = 0
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rot = int(float(sd["rotation"]))
            except (TypeError, ValueError):
                pass
    return abs(rot) % 360


def parse_probe(data: dict) -> MediaInfo:
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise errors.video_no_video_stream()
    duration = fmt.get("duration") or video.get("duration")
    try:
        duration_ms = int(round(float(duration) * 1000))
    except (TypeError, ValueError):
        raise errors.video_unreadable("missing duration")
    if duration_ms <= 0:
        raise errors.video_unreadable("zero duration")
    fps, fps_str = _fps(video)
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise errors.video_unreadable("missing dimensions")
    size = fmt.get("size")
    return MediaInfo(
        duration_ms=duration_ms,
        width=width,
        height=height,
        fps=round(fps, 3),
        fps_str=fps_str,
        rotation=_rotation(video),
        has_audio=audio is not None,
        video_codec=str(video.get("codec_name") or "unknown"),
        audio_codec=str(audio.get("codec_name")) if audio else None,
        container=str(fmt.get("format_name") or ""),
        size_bytes=int(size) if size else None,
        pix_fmt=video.get("pix_fmt"),
    )


async def probe(src: str, timeout: float = 120) -> MediaInfo:
    cmd = ["ffprobe", "-v", "error", *input_opts(src), "-print_format", "json", "-show_format", "-show_streams", src]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise errors.storage_failed("ffprobe timed out reading the source")
    if proc.returncode != 0:
        raise errors.video_unreadable(err.decode(errors="replace"))
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        raise errors.video_unreadable("ffprobe returned invalid JSON")
    return parse_probe(data)


# ------------------------------------------------------------ common tasks

async def extract_audio(src: str, dest: Path, *, duration_ms: int, on_progress: ProgressCb | None = None) -> None:
    """Mono, speech-optimised, compressed audio (no WAV) for the transcription provider."""
    await run_ffmpeg(
        [*input_opts(src), "-i", src, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k", str(dest)],
        duration_ms=duration_ms,
        on_progress=on_progress,
        timeout=max(600, duration_ms / 1000),
    )


async def extract_frame(src: str, dest: Path, at_ms: int, width: int = 640) -> None:
    await run_ffmpeg(
        ["-ss", f"{at_ms / 1000:.3f}", *input_opts(src), "-i", src, "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2", "-q:v", "4", str(dest)],
        timeout=120,
    )


async def make_proxy(src: str, dest: Path, *, duration_ms: int, on_progress: ProgressCb | None = None, threads: int = 0) -> None:
    """Browser-compatible 720p H.264 proxy, only generated when the source can't play in browsers."""
    await run_ffmpeg(
        [
            *input_opts(src), "-i", src,
            "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", "scale=-2:'min(720,ih)'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-ac", "2",
            "-movflags", "+faststart",
            str(dest),
        ],
        duration_ms=duration_ms,
        on_progress=on_progress,
        timeout=max(1800, duration_ms / 1000 * 2),
        threads=threads,
    )
