import random

from clipforest_worker.vision.detector import Box
from clipforest_worker.vision.reframe import Sample, crop_dims, interpolate, plan_framing, sendcmd_script

W, H = 1920, 1080
FPS = 3.0


def plan(samples, mode="auto", **kw):
    return plan_framing(mode, samples, W, H, sample_fps=FPS, min_coverage=kw.get("min_coverage", 0.3), fallback=kw.get("fallback", "center"), dwell_ms=2500)


def face(cx, cy=0.4, size=0.12):
    return Box(cx - size / 2, cy - size / 2, size, size * 16 / 9, 0.9)


def test_crop_dims_for_common_sources():
    assert crop_dims(1920, 1080) == (608, 1080)
    assert crop_dims(1080, 1920) == (1080, 1920)
    assert crop_dims(1080, 1080) == (608, 1080)
    assert crop_dims(3840, 2160) == (1216, 2160)
    assert crop_dims(720, 1600) == (720, 1280)


def test_single_face_is_followed_smoothly_and_stays_in_bounds():
    rng = random.Random(1)
    samples = [Sample(int(i * 1000 / FPS), [face(0.7 + rng.uniform(-0.01, 0.01))]) for i in range(90)]
    p = plan(samples)
    assert p.strategy == "auto"
    cw = p.crop_w
    xs = [x for _, x, _ in p.keyframes]
    assert all(0 <= x <= W - cw for x in xs)
    assert all(0 <= y <= H - p.crop_h for _, _, y in p.keyframes)
    # Detector jitter (+-19 px) must not translate into frame-to-frame shaking.
    steps = [abs(xs[i] - xs[i - 1]) for i in range(1, len(xs))]
    assert max(steps[5:]) <= 6
    center = xs[-1] + cw / 2
    assert abs(center - 0.7 * W) < 0.06 * W
    assert p.stats["faceInFrameRatio"] >= 0.95


def test_face_near_edge_is_clamped_not_out_of_bounds():
    samples = [Sample(int(i * 1000 / FPS), [face(0.02)]) for i in range(30)]
    p = plan(samples)
    assert all(x == 0 for _, x, _ in p.keyframes[3:])


def test_no_faces_falls_back_to_configured_layout():
    samples = [Sample(int(i * 1000 / FPS), []) for i in range(30)]
    center = plan(samples)
    assert center.strategy == "center" and center.stats["reason"] == "low-face-coverage"
    assert center.keyframes == [(0.0, (W - center.crop_w) // 2, 0)]
    fit = plan(samples, fallback="fit")
    assert fit.strategy == "fit"


def test_flickering_second_face_does_not_cause_rapid_jumps():
    samples = []
    for i in range(60):
        boxes = [face(0.3)]
        if i % 2 == 0:
            boxes.append(face(0.8, size=0.1))
        samples.append(Sample(int(i * 1000 / FPS), boxes))
    p = plan(samples)
    assert p.stats["switches"] == 0
    xs = [x for _, x, _ in p.keyframes]
    assert max(abs(xs[i] - xs[i - 1]) for i in range(1, len(xs))) <= 0.6 / FPS * p.crop_w * 1.5 + 1


def test_center_and_fit_modes_skip_analysis():
    assert plan([], mode="center").strategy == "center"
    assert plan([], mode="fit").strategy == "fit"
    portrait = plan_framing("auto", [Sample(0, [face(0.5)])], 1080, 1920, sample_fps=FPS, min_coverage=0.3, fallback="center", dwell_ms=2500)
    assert portrait.strategy == "center" and portrait.stats["reason"] == "source-already-vertical"


def test_interpolation_and_sendcmd_script():
    keyframes = [(0.0, 0, 0), (1.0, 100, 0), (2.0, 100, 0)]
    frames = interpolate(keyframes, 2.0, 30.0)
    assert len(frames) == 61
    assert frames[15][1] == 50
    script = sendcmd_script(frames)
    lines = script.strip().splitlines()
    assert lines[0] == "0.000 crop x 0, crop y 0;"
    assert all(line.endswith(";") for line in lines)
    assert len(lines) <= 32  # unchanged positions are not repeated
