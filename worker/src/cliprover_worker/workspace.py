"""Per-job working directories, disk guard and local janitor (PRD §16.2-16.3)."""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from . import errors
from .log import get_logger
from .metrics import TEMP_DISK_FREE, TEMP_DISK_HIGH_WATER

log = get_logger(__name__)
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_ACTIVE: set[str] = set()
_high_water = 0


def disk_free(root: str) -> int:
    Path(root).mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free
    TEMP_DISK_FREE.set(free)
    return free


def ensure_disk(root: str, reserve_bytes: int, needed_bytes: int = 0) -> None:
    """Fail (retryably) before starting heavy work that would breach the free-space reserve."""
    free = disk_free(root)
    if free - needed_bytes < reserve_bytes:
        raise errors.low_disk(free, needed_bytes + reserve_bytes)


def dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


class Workspace:
    """An isolated directory owned by one job; always removed in the cleanup path."""

    def __init__(self, root: str, job_id: str):
        self.name = _SAFE.sub("_", job_id)[:180]
        self.path = Path(root) / self.name

    def __enter__(self) -> "Workspace":
        # A retried job starts from a clean directory.
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
        self.path.mkdir(parents=True, exist_ok=True)
        _ACTIVE.add(str(self.path))
        return self

    def file(self, name: str) -> Path:
        return self.path / name

    def cleanup(self) -> None:
        global _high_water
        try:
            size = dir_size(self.path)
            if size > _high_water:
                _high_water = size
                TEMP_DISK_HIGH_WATER.set(size)
        except OSError:
            pass
        shutil.rmtree(self.path, ignore_errors=True)
        _ACTIVE.discard(str(self.path))

    def __exit__(self, *exc) -> None:
        self.cleanup()


def sweep_stale(root: str, max_age_hours: float) -> int:
    """Remove abandoned working directories older than the safety threshold."""
    base = Path(root)
    if not base.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for child in base.iterdir():
        if str(child) in _ACTIVE:
            continue
        try:
            if child.stat().st_mtime < cutoff:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    if removed:
        log.info("Removed stale working directories", extra={"count": removed})
    disk_free(root)
    return removed


def active_count() -> int:
    return len(_ACTIVE)


__all__ = ["Workspace", "ensure_disk", "sweep_stale", "disk_free", "dir_size", "active_count", "os"]
