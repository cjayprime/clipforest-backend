"""Subject-aware framing: face detection, clip-local frame sampling (never the full source, PRD §9.4) and the smoothed virtual-camera crop path."""

from __future__ import annotations
import os
from dataclasses import dataclass, field
import numpy as np
import asyncio
from typing import AsyncIterator
from .log import get_logger
from .ffmpeg import input_opts


log = get_logger(__name__)


@dataclass
class Box:
    """Normalized (0..1) box relative to the analyzed frame."""

    x: float
    y: float
    w: float
    h: float
    score: float = 1.0

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h


def box_iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


class FaceDetector:
    def __init__(self, model_path: str, score_threshold: float = 0.6):
        import cv2

        self.cv2 = cv2
        self.kind = "none"
        self._yunet = None
        self._haar = None
        if model_path and os.path.exists(model_path) and hasattr(cv2, "FaceDetectorYN"):
            try:
                self._yunet = cv2.FaceDetectorYN.create(model_path, "", (320, 320), score_threshold, 0.3, 50)
                self.kind = "yunet"
            except Exception as exc:  # noqa: BLE001
                log.warning("YuNet unavailable, falling back to Haar", extra={"error": str(exc)})
        if self._yunet is None:
            cascade = os.path.join(getattr(cv2, "data", None).haarcascades, "haarcascade_frontalface_default.xml") if hasattr(cv2, "data") else ""
            if cascade and os.path.exists(cascade):
                self._haar = cv2.CascadeClassifier(cascade)
                self.kind = "haar"
        if self.kind == "none":
            log.warning("No face detector available; renders will use the fallback framing")

    def detect(self, frame: np.ndarray) -> list[Box]:
        h, w = frame.shape[:2]
        if self._yunet is not None:
            self._yunet.setInputSize((w, h))
            _, faces = self._yunet.detect(frame)
            if faces is None:
                return []
            return [Box(float(f[0]) / w, float(f[1]) / h, float(f[2]) / w, float(f[3]) / h, float(f[14])) for f in faces if f[2] > 0 and f[3] > 0]
        if self._haar is not None:
            gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
            rects = self._haar.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6, minSize=(max(24, w // 30), max(24, w // 30)))
            return [Box(x / w, y / h, bw / w, bh / h, 0.8) for (x, y, bw, bh) in rects]
        return []


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


HEADROOM = 0.38  # face centre sits at 38% of the crop height from the top


@dataclass
class Sample:
    t_ms: int
    boxes: list[Box]


@dataclass
class Track:
    id: int
    points: dict[int, Box] = field(default_factory=dict)

    def last(self) -> tuple[int, Box]:
        k = max(self.points)
        return k, self.points[k]


@dataclass
class FramingPlan:
    strategy: str  # auto | center | fit
    crop_w: int
    crop_h: int
    keyframes: list[tuple[float, int, int]]  # (t seconds, x, y) of crop top-left in source pixels
    stats: dict


def even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def crop_dims(src_w: int, src_h: int, aspect: float = 9 / 16) -> tuple[int, int]:
    if src_w / src_h > aspect:
        return min(src_w, even(src_h * aspect)), src_h // 2 * 2
    return src_w // 2 * 2, min(src_h, even(src_w / aspect))


def build_tracks(samples: list[Sample], iou_thr: float = 0.25, max_gap: int = 4) -> list[Track]:
    tracks: list[Track] = []
    for i, s in enumerate(samples):
        claimed: set[int] = set()
        for b in sorted(s.boxes, key=lambda b: -b.area):
            best, best_score = None, 0.0
            for t in tracks:
                if t.id in claimed:
                    continue
                k, last = t.last()
                if i - k > max_gap:
                    continue
                dist = ((last.cx - b.cx) ** 2 + (last.cy - b.cy) ** 2) ** 0.5
                score = box_iou(last, b) + max(0.0, 0.3 - dist)
                if score > best_score and (box_iou(last, b) >= iou_thr or dist < max(last.w, b.w) * 0.8):
                    best, best_score = t, score
            if best is None:
                best = Track(len(tracks))
                tracks.append(best)
            best.points[i] = b
            claimed.add(best.id)
    return tracks


def choose_subject(tracks: list[Track], n: int, sample_fps: float, dwell_ms: int) -> tuple[list[Box | None], int]:
    """Per-sample subject box and number of subject switches."""
    if not tracks or n == 0:
        return [None] * n, 0

    def rank(t: Track) -> float:
        vis = len(t.points) / n
        cent = 1 - min(1.0, 2 * abs(sum(b.cx for b in t.points.values()) / len(t.points) - 0.5))
        area = sum(b.area for b in t.points.values()) / len(t.points)
        return vis * 0.6 + cent * 0.25 + min(1.0, area * 8) * 0.15

    ordered = sorted(tracks, key=rank, reverse=True)
    dwell = max(1, int(round(dwell_ms / 1000 * sample_fps)))
    current = ordered[0]
    out: list[Box | None] = []
    switches = 0
    absent = 0
    candidate: Track | None = None
    candidate_run = 0
    for i in range(n):
        if i in current.points:
            absent, candidate, candidate_run = 0, None, 0
            out.append(current.points[i])
            continue
        absent += 1
        visible = [t for t in ordered if i in t.points]
        if visible:
            best = visible[0]
            candidate_run = candidate_run + 1 if candidate is best else 1
            candidate = best
            if absent >= dwell and candidate_run >= dwell:
                current = best
                switches += 1
                absent, candidate, candidate_run = 0, None, 0
                out.append(current.points[i])
                continue
        out.append(None)
    return out, switches


def _fill(values: list[float | None], default: float) -> list[float]:
    known = [v for v in values if v is not None]
    if not known:
        return [default] * len(values)
    out: list[float] = []
    last = next(v for v in values if v is not None)
    for v in values:
        last = v if v is not None else last
        out.append(last)
    return out


def smooth(values: list[float], *, deadzone: float, alpha: float, max_step: float) -> list[float]:
    if not values:
        return []
    # Dead-zone: ignore movements smaller than the threshold (detector jitter).
    held = [values[0]]
    for v in values[1:]:
        held.append(v if abs(v - held[-1]) > deadzone else held[-1])
    # Zero-phase EMA (forward + backward) so the camera doesn't lag the subject.
    fwd = [held[0]]
    for v in held[1:]:
        fwd.append(fwd[-1] + alpha * (v - fwd[-1]))
    bwd = [fwd[-1]]
    for v in reversed(fwd[:-1]):
        bwd.append(bwd[-1] + alpha * (v - bwd[-1]))
    ema = list(reversed(bwd))
    # Velocity limit.
    out = [ema[0]]
    for v in ema[1:]:
        step = max(-max_step, min(max_step, v - out[-1]))
        out.append(out[-1] + step)
    return out


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def plan_framing(
    mode: str,
    samples: list[Sample],
    src_w: int,
    src_h: int,
    *,
    sample_fps: float,
    min_coverage: float,
    fallback: str,
    dwell_ms: int,
    aspect: float = 9 / 16,
) -> FramingPlan:
    cw, ch = crop_dims(src_w, src_h, aspect)
    center = ((src_w - cw) // 2, (src_h - ch) // 2)
    if mode == "fit":
        return FramingPlan("fit", cw, ch, [], {"reason": "requested"})
    if mode == "center" or not samples:
        return FramingPlan("center", cw, ch, [(0.0, *center)], {"reason": "requested" if mode == "center" else "no-samples"})
    if cw >= src_w and ch >= src_h:
        return FramingPlan("center", cw, ch, [(0.0, 0, 0)], {"reason": "source-matches-aspect"})

    tracks = build_tracks(samples)
    chosen, switches = choose_subject(tracks, len(samples), sample_fps, dwell_ms)
    coverage = sum(b is not None for b in chosen) / len(samples)
    stats: dict = {"faceCoverage": round(coverage, 3), "tracks": len(tracks), "switches": switches, "samples": len(samples)}
    if coverage < min_coverage:
        strategy = "fit" if fallback == "fit" else "center"
        stats["reason"] = "low-face-coverage"
        return FramingPlan(strategy, cw, ch, [] if strategy == "fit" else [(0.0, *center)], stats)

    max_x, max_y = src_w - cw, src_h - ch
    tx = _fill([None if b is None else b.cx * src_w - cw / 2 for b in chosen], center[0])
    ty = _fill([None if b is None else b.cy * src_h - HEADROOM * ch for b in chosen], center[1])
    per_sample_speed = 0.6 / max(sample_fps, 0.1)  # at most 60% of the crop width per second
    xs = smooth([clamp(v, 0, max_x) for v in tx], deadzone=0.04 * cw, alpha=0.3, max_step=per_sample_speed * cw)
    ys = smooth([clamp(v, 0, max_y) for v in ty], deadzone=0.05 * ch, alpha=0.3, max_step=per_sample_speed * ch)

    # Safe framing: keep the tracked face fully inside the crop where possible, then re-limit velocity.
    margin = 0.04 * cw
    for i, b in enumerate(chosen):
        if b is None:
            continue
        fx0, fx1 = b.x * src_w, (b.x + b.w) * src_w
        if fx0 < xs[i] + margin:
            xs[i] = fx0 - margin
        elif fx1 > xs[i] + cw - margin:
            xs[i] = fx1 + margin - cw
    xs = smooth([clamp(v, 0, max_x) for v in xs], deadzone=0, alpha=1.0, max_step=per_sample_speed * cw * 1.5)

    keyframes = [(s.t_ms / 1000, int(round(clamp(x, 0, max_x))), int(round(clamp(y, 0, max_y)))) for s, x, y in zip(samples, xs, ys)]
    inside = sum(
        1
        for b, (_, x, y) in zip(chosen, keyframes)
        if b is not None and b.x * src_w >= x - 1 and (b.x + b.w) * src_w <= x + cw + 1
    )
    jumps = [abs(keyframes[i][1] - keyframes[i - 1][1]) for i in range(1, len(keyframes))]
    stats.update(
        {
            "faceInFrameRatio": round(inside / max(1, sum(b is not None for b in chosen)), 3),
            "maxStepPx": max(jumps) if jumps else 0,
            "reason": "tracked",
        }
    )
    return FramingPlan("auto", cw, ch, keyframes, stats)


def interpolate(keyframes: list[tuple[float, int, int]], duration_s: float, out_fps: float) -> list[tuple[float, int, int]]:
    """Per-output-frame crop positions, linearly interpolated between analysis samples."""
    if not keyframes:
        return []
    if len(keyframes) == 1:
        return [keyframes[0]]
    frames: list[tuple[float, int, int]] = []
    n = max(1, int(duration_s * out_fps))
    k = 0
    for f in range(n + 1):
        t = f / out_fps
        while k + 1 < len(keyframes) and keyframes[k + 1][0] <= t:
            k += 1
        if k + 1 >= len(keyframes):
            frames.append((t, keyframes[-1][1], keyframes[-1][2]))
            continue
        t0, x0, y0 = keyframes[k]
        t1, x1, y1 = keyframes[k + 1]
        a = 0.0 if t1 <= t0 else max(0.0, min(1.0, (t - t0) / (t1 - t0)))
        frames.append((t, int(round(x0 + (x1 - x0) * a)), int(round(y0 + (y1 - y0) * a))))
    return frames


def sendcmd_script(frames: list[tuple[float, int, int]], target: str = "crop") -> str:
    """FFmpeg sendcmd script updating the crop filter's x/y only when they change."""
    lines: list[str] = []
    last: tuple[int, int] | None = None
    for t, x, y in frames:
        if last == (x, y):
            continue
        lines.append(f"{t:.3f} {target} x {x}, {target} y {y};")
        last = (x, y)
    return "\n".join(lines) + "\n"
