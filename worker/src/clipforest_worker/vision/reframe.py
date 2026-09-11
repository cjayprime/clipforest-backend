"""Subject tracking and smooth virtual-camera paths (PRD §9.4-9.7).

  * detections are associated into tracks across samples (identity, less jitter)
  * the MVP heuristic picks the most consistently visible, most central face and
    only switches subjects after a minimum dwell time
  * raw coordinates are never used directly: dead-zone + zero-phase EMA + velocity
    limit, then clamped so no crop ever leaves the source frame
  * low face coverage falls back to center crop or fit-with-background
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .detector import Box, box_iou

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
) -> FramingPlan:
    cw, ch = crop_dims(src_w, src_h)
    center = ((src_w - cw) // 2, (src_h - ch) // 2)
    if mode == "fit":
        return FramingPlan("fit", cw, ch, [], {"reason": "requested"})
    if mode == "center" or not samples:
        return FramingPlan("center", cw, ch, [(0.0, *center)], {"reason": "requested" if mode == "center" else "no-samples"})
    if cw >= src_w and ch >= src_h:
        return FramingPlan("center", cw, ch, [(0.0, 0, 0)], {"reason": "source-already-vertical"})

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
