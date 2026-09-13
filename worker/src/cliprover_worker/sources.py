"""Source adapters (PRD §5 FR-ING-002). Provider-specific download logic lives here,
never in core job code. URLs are untrusted: hosts are allow-listed and the URL is
rebuilt from the parsed video ID before it reaches yt-dlp (SSRF defence)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import parse_qs, urlparse

from . import errors
from .config import Settings
from .log import get_logger

log = get_logger(__name__)

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_UNAVAILABLE = ("private video", "sign in", "age", "unavailable", "not available", "removed", "copyright", "members-only", "premieres", "live event")


def youtube_id(url: str) -> str:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme not in {"http", "https"} or host not in YOUTUBE_HOSTS or u.port or u.username:
        raise errors.source_unsupported(f"host {host!r} is not an allowed source")
    if host == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
    elif u.path == "/watch":
        vid = (parse_qs(u.query).get("v") or [""])[0]
    else:
        m = re.match(r"^/(?:shorts|live|embed)/([^/?#]+)", u.path)
        vid = m.group(1) if m else ""
    if not _YT_ID.match(vid or ""):
        raise errors.source_unsupported("no valid YouTube video id")
    return vid


async def download_youtube(
    url: str, workdir: Path, s: Settings, on_progress: Callable[[float], Awaitable[None]]
) -> tuple[Path, dict]:
    import yt_dlp  # imported lazily: only URL ingestion needs it

    vid = youtube_id(url)
    canonical = f"https://www.youtube.com/watch?v={vid}"
    state = {"frac": 0.0}

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                state["frac"] = min(0.99, d.get("downloaded_bytes", 0) / total)

    base_opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 30,
        "retries": 3,
        "allowed_extractors": ["youtube"],
    }

    def _probe() -> dict:
        with yt_dlp.YoutubeDL(base_opts) as ydl:
            return ydl.extract_info(canonical, download=False)

    def _download() -> dict:
        opts = {
            **base_opts,
            "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[ext=mp4][height<=1080]/bv*[height<=1080]+ba/b",
            "merge_output_format": "mp4",
            "outtmpl": str(workdir / "source.%(ext)s"),
            "max_filesize": s.max_upload_bytes,
            "progress_hooks": [hook],
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(canonical, download=True)

    def _map(exc: Exception) -> errors.PipelineError:
        msg = str(exc).lower()
        if any(k in msg for k in _UNAVAILABLE):
            return errors.source_unavailable(str(exc))
        return errors.PipelineError("VIDEO_SOURCE_DOWNLOAD_FAILED", "Downloading the video failed. We'll retry.", retryable=True, details={"detail": str(exc)[-500:]})

    try:
        info = await asyncio.to_thread(_probe)
    except yt_dlp.utils.DownloadError as exc:
        raise _map(exc) from exc
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
        raise errors.source_unavailable("live streams are not supported")
    duration = int(info.get("duration") or 0)
    if duration and duration > s.max_video_duration_sec:
        raise errors.video_too_long(s.max_video_duration_sec)

    task = asyncio.create_task(asyncio.to_thread(_download))
    try:
        while not task.done():
            await asyncio.sleep(2)
            await on_progress(state["frac"])
        info = task.result()
    except yt_dlp.utils.DownloadError as exc:
        raise _map(exc) from exc
    files = sorted(workdir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        raise errors.source_unavailable("download produced no file")
    return files[0], {"id": vid, "title": info.get("title"), "duration": duration, "uploader": info.get("uploader")}
