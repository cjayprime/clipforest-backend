"""Render inputs that need no FFmpeg: caption grouping and ASS output, and subject-aware framing plans."""

from __future__ import annotations
import re
from cliprover_worker.captions import ass_time, build_ass, clip_words, escape, get_preset, group_phrases, PRESETS, line_chars
from cliprover_worker.transcription.base import Word
from helpers import words_for
import random
from cliprover_worker.vision import Box, crop_dims, interpolate, plan_framing, Sample, sendcmd_script


WORDS = words_for("So um the the biggest mistake we made was hiring too quickly. Then everything changed for us.", 10_000)


def test_clip_local_timestamps_and_cleanup():
    cw = clip_words(WORDS, 10_000, 40_000)
    texts = [w.text for w in cw]
    assert "um" not in texts
    assert texts[:3] == ["So", "the", "biggest"]  # stutter "the the" collapsed
    assert cw[0].start_ms == WORDS[0].start_ms - 10_000
    assert all(0 <= w.start_ms < w.end_ms <= 30_000 for w in cw)


def test_words_outside_clip_are_excluded_and_clamped():
    cw = clip_words(WORDS, WORDS[3].start_ms + 50, WORDS[6].end_ms - 50)
    assert cw[0].start_ms == 0
    assert cw[-1].end_ms <= WORDS[6].end_ms - 50 - (WORDS[3].start_ms + 50)


def test_grouping_timing_rules():
    cw = clip_words(WORDS, 10_000, 40_000)
    phrases = group_phrases(cw, max_chars_per_line=17, max_words=5, clip_length_ms=30_000)
    assert phrases
    for i, p in enumerate(phrases):
        assert len(p.words) <= 6
        assert p.start_ms == p.words[0].start_ms  # never before the first word is spoken
        assert p.end_ms >= p.start_ms
        if i + 1 < len(phrases):
            assert p.end_ms <= phrases[i + 1].start_ms
        assert 1 <= len(p.lines) <= 2
        for line in p.lines:
            text = " ".join(w.text for w in line)
            assert len(text) <= 17 or len(line) == 1


def test_sentence_boundaries_break_phrases():
    cw = clip_words(WORDS, 10_000, 40_000)
    phrases = group_phrases(cw, max_chars_per_line=40, max_words=20, clip_length_ms=30_000)
    assert any(p.words[-1].text.endswith(".") for p in phrases)
    assert phrases[-1].words[0].text != "quickly."


def test_ass_active_word_emphasis_advances_monotonically():
    preset = get_preset("bold-default")
    cw = clip_words(WORDS, 10_000, 40_000)
    phrases = group_phrases(cw, max_chars_per_line=preset.max_chars, max_words=preset.max_words, clip_length_ms=30_000)
    ass = build_ass(phrases, preset)
    events = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(events) == sum(len(p.words) for p in phrases)
    starts = [l.split(",")[1] for l in events]
    assert starts == sorted(starts)
    emphasis = "\\c&H0AD6FF&"  # #FFD60A in ASS BGR order
    for l in events:
        assert l.count(emphasis) == 1  # exactly one highlighted word per event
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass


def _style(ass: str) -> list[str]:
    return next(line for line in ass.splitlines() if line.startswith("Style:")).split(",")


def test_caption_layout_scales_to_the_output_frame():
    preset = get_preset("bold-default")
    phrases = group_phrases(clip_words(WORDS, 10_000, 40_000), max_chars_per_line=preset.max_chars, max_words=preset.max_words)
    portrait = _style(build_ass(phrases, preset, 1080, 1920))
    landscape = _style(build_ass(phrases, preset, 1920, 1080))
    square = _style(build_ass(phrases, preset, 1080, 1080))
    # MarginL, MarginR, MarginV are the three fields before Encoding.
    assert portrait[-4:-1] == ["90", "90", str(preset.margin_v)]
    assert landscape[-4:-1] == ["160", "160", str(round(preset.margin_v * 1080 / 1920))]
    assert square[-4:-1] == ["90", "90", str(round(preset.margin_v * 1080 / 1920))]
    assert portrait[2] == landscape[2] == square[2] == str(preset.size)
    assert line_chars(preset, 1080) == preset.max_chars
    assert line_chars(preset, 1920) > preset.max_chars


def test_minimal_preset_has_no_word_emphasis():
    preset = get_preset("minimal")
    cw = clip_words(WORDS, 10_000, 40_000)
    phrases = group_phrases(cw, max_chars_per_line=preset.max_chars, max_words=preset.max_words)
    ass = build_ass(phrases, preset)
    events = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(events) == len(phrases)
    assert "\\c&H" not in ass


def test_long_words_are_scaled_to_stay_inside_safe_margins():
    preset = get_preset("impact")
    cw = clip_words([Word(0, 900, "Supercalifragilisticexpialidocious")], 0, 5_000)
    phrases = group_phrases(cw, max_chars_per_line=preset.max_chars, max_words=preset.max_words)
    ass = build_ass(phrases, preset)
    m = re.search(r"\\fscx(\d+)\\fscy\d+\}", ass)
    assert m and int(m.group(1)) < 100


def test_escaping_and_time_format():
    assert escape("{\\an8}hello") == "(an8)hello"
    assert ass_time(3_723_450) == "1:02:03.45"
    assert set(PRESETS) == {"bold-default", "karaoke", "minimal", "impact"}
    assert get_preset("does-not-exist").name == "bold-default"


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
    assert portrait.strategy == "center" and portrait.stats["reason"] == "source-matches-aspect"


def test_crop_follows_the_requested_aspect_ratio():
    assert crop_dims(1920, 1080, 1.0) == (1080, 1080)
    assert crop_dims(1920, 1080, 4 / 5) == (864, 1080)
    assert crop_dims(1920, 1080, 16 / 9) == (1920, 1080)
    assert crop_dims(1080, 1920, 16 / 9) == (1080, 608)
    square = plan_framing("center", [], 1920, 1080, sample_fps=FPS, min_coverage=0.3, fallback="center", dwell_ms=2500, aspect=1.0)
    assert (square.crop_w, square.crop_h) == (1080, 1080) and square.keyframes == [(0.0, 420, 0)]


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
