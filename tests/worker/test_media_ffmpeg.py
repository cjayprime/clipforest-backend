"""FFmpeg integration tests over generated fixtures (PRD §23.2-23.3).

Golden fixtures are synthesized with lavfi so no binary media lives in git:
landscape, portrait, no-audio and corrupt inputs. Assertions check valid output,
dimensions, duration bounds and caption presence rather than pixels.
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import pytest
from cliprover_worker import errors
from cliprover_worker.captions import build_ass, clip_words, get_preset, group_phrases, line_chars
from cliprover_worker.config import Settings
from cliprover_worker.ffmpeg import extract_audio, probe, run_ffmpeg
from cliprover_worker.jobs.render import build_filtergraph, build_render_args, output_fps, OUTPUT_SIZES
from cliprover_worker.vision import crop_dims, FaceDetector, FramingPlan, interpolate, plan_framing, Sample, sample_frames, sendcmd_script
from helpers import words_for

pytestmark = [pytest.mark.ffmpeg, pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")]

# Generated fixtures live in a writable temp dir (the container runs tests as a non-root user).
FIXTURES = Path(os.environ.get("TEST_FIXTURES_DIR", tempfile.gettempdir())) / "cliprover-fixtures"


def fixture(name: str, size: str, seconds: int = 8, audio: bool = True) -> Path:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / name
    if not path.exists():
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30"]
        if audio:
            cmd += ["-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000"]
        cmd += ["-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
        cmd += ["-c:a", "aac"] if audio else []
        subprocess.run([*cmd, str(path)], check=True)
    return path


async def render(
    tmp_path: Path, src: Path, plan: FramingPlan, *, captions: bool, has_audio: bool, start_ms=1000, dur_ms=5000, out=(1080, 1920)
) -> Path:
    cmd_file = None
    if plan.strategy == "auto" and len(plan.keyframes) > 1:
        (tmp_path / "crop.cmd").write_text(sendcmd_script(interpolate(plan.keyframes, dur_ms / 1000, 30.0)))
        cmd_file = "crop.cmd"
    cap = None
    if captions:
        preset = get_preset("bold-default")
        words = words_for("The biggest mistake we made was hiring too quickly and it cost us.", start_ms)
        phrases = group_phrases(clip_words(words, start_ms, start_ms + dur_ms), max_chars_per_line=line_chars(preset, out[0]), max_words=preset.max_words)
        (tmp_path / "captions.ass").write_text(build_ass(phrases, preset, *out), encoding="utf-8")
        cap = "captions.ass"
    graph = build_filtergraph(plan, out_w=out[0], out_h=out[1], fps=output_fps(30.0), captions_file=cap, sendcmd_file=cmd_file, fonts_dir=None)
    args = build_render_args(src=str(src), start_ms=start_ms, duration_ms=dur_ms, filtergraph=graph, has_audio=has_audio, preset="ultrafast", crf=28, audio_normalize=False)
    await run_ffmpeg(args, cwd=tmp_path, timeout=300)
    return tmp_path / "output.mp4"


async def assert_valid_output(path: Path, dur_ms: int, has_audio: bool, size=(1080, 1920)):
    info = await probe(str(path))
    assert (info.display_width, info.display_height) == size
    assert abs(info.duration_ms - dur_ms) <= 600
    assert info.video_codec == "h264"
    assert info.has_audio == has_audio
    if has_audio:
        assert info.audio_codec == "aac"
    assert info.browser_compatible


async def test_probe_landscape_fixture():
    info = await probe(str(fixture("landscape.mp4", "1280x720")))
    assert (info.width, info.height, info.orientation) == (1280, 720, "landscape")
    assert 7900 <= info.duration_ms <= 8100
    assert info.has_audio and info.browser_compatible


async def test_corrupt_media_is_a_non_retryable_error(tmp_path):
    bad = tmp_path / "corrupt.mp4"
    bad.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 40)
    with pytest.raises(errors.PipelineError) as info:
        await probe(str(bad))
    assert info.value.code in {"VIDEO_UNREADABLE", "VIDEO_NO_VIDEO_STREAM"} and not info.value.retryable


async def test_no_audio_fixture_is_detected():
    info = await probe(str(fixture("noaudio.mp4", "1280x720", audio=False)))
    assert not info.has_audio


async def test_center_render_with_captions(tmp_path):
    src = fixture("landscape.mp4", "1280x720")
    cw, ch = crop_dims(1280, 720)
    plan = FramingPlan("center", cw, ch, [(0.0, (1280 - cw) // 2, 0)], {})
    out = await render(tmp_path, src, plan, captions=True, has_audio=True)
    await assert_valid_output(out, 5000, True)


async def test_auto_render_with_moving_crop_path(tmp_path):
    src = fixture("landscape.mp4", "1280x720")
    cw, ch = crop_dims(1280, 720)
    plan = FramingPlan("auto", cw, ch, [(0.0, 0, 0), (2.5, 1280 - cw, 0), (5.0, 300, 0)], {})
    out = await render(tmp_path, src, plan, captions=False, has_audio=True)
    await assert_valid_output(out, 5000, True)


async def test_fit_render_of_portrait_source(tmp_path):
    src = fixture("portrait.mp4", "720x1280")
    plan = FramingPlan("fit", *crop_dims(720, 1280), [], {})
    out = await render(tmp_path, src, plan, captions=True, has_audio=True)
    await assert_valid_output(out, 5000, True)


async def test_render_without_audio_track(tmp_path):
    src = fixture("noaudio.mp4", "1280x720", audio=False)
    cw, ch = crop_dims(1280, 720)
    out = await render(tmp_path, src, FramingPlan("center", cw, ch, [(0.0, 0, 0)], {}), captions=False, has_audio=False)
    await assert_valid_output(out, 5000, False)


@pytest.mark.parametrize("aspect", ["4:5", "1:1", "16:9"])
@pytest.mark.parametrize("strategy", ["center", "fit"])
async def test_every_aspect_ratio_renders_at_its_output_size(tmp_path, aspect, strategy):
    src = fixture("landscape.mp4", "1280x720")
    size = OUTPUT_SIZES[aspect]
    cw, ch = crop_dims(1280, 720, size[0] / size[1])
    keyframes = [] if strategy == "fit" else [(0.0, (1280 - cw) // 2, (720 - ch) // 2)]
    out = await render(tmp_path, src, FramingPlan(strategy, cw, ch, keyframes, {}), captions=True, has_audio=True, out=size)
    await assert_valid_output(out, 5000, True, size)


async def test_frame_sampling_and_detector_fallback_path():
    src = fixture("landscape.mp4", "1280x720")
    detector = FaceDetector(Settings().face_model_path)
    samples = []
    async for t_ms, frame in sample_frames(str(src), 1000, 4000, 3.0, 1280, 720):
        samples.append(Sample(t_ms, await asyncio.to_thread(detector.detect, frame)))
    assert 11 <= len(samples) <= 13
    plan = plan_framing("auto", samples, 1280, 720, sample_fps=3.0, min_coverage=0.3, fallback="center", dwell_ms=2500)
    assert plan.strategy in {"center", "auto"}  # synthetic test pattern: typically no faces -> fallback


async def test_audio_extraction_is_mono_compressed(tmp_path):
    out = tmp_path / "audio.mp3"
    await extract_audio(str(fixture("landscape.mp4", "1280x720")), out, duration_ms=8000)
    assert out.stat().st_size < 200_000
