"""FFmpeg command construction for the final render (PRD §9.2-9.3).

One decode of the selected range, one final encode: crop/scale (or blurred-fit),
optional burned-in ASS captions, H.264 + AAC, 1080x1920, +faststart, metadata
stripped. Relative filenames are used with cwd = the job workspace so nothing
user-controlled needs escaping.
"""

from __future__ import annotations

from ..media.ffmpeg import input_opts
from ..vision.reframe import FramingPlan

OUT_W, OUT_H = 1080, 1920


def output_fps(source_fps: float | None) -> str:
    if source_fps and 23.0 <= source_fps <= 60.5:
        return f"{source_fps:.3f}".rstrip("0").rstrip(".")
    return "30"


def build_filtergraph(plan: FramingPlan, *, fps: str, captions_file: str | None, sendcmd_file: str | None, fonts_dir: str | None) -> str:
    head = f"[0:v]setpts=PTS-STARTPTS,fps={fps}"
    if plan.strategy == "fit":
        graph = (
            f"{head},split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,gblur=sigma=10,"
            f"scale={OUT_W}:{OUT_H},eq=brightness=-0.06[bg];"
            f"[fgsrc]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    else:
        x0, y0 = (plan.keyframes[0][1], plan.keyframes[0][2]) if plan.keyframes else (0, 0)
        cmd = f"sendcmd=f={sendcmd_file}," if sendcmd_file else ""
        graph = f"{head},{cmd}crop=w={plan.crop_w}:h={plan.crop_h}:x={x0}:y={y0},scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1"
    if captions_file:
        graph += f",ass={captions_file}" + (f":fontsdir={fonts_dir}" if fonts_dir else "")
    return graph + ",format=yuv420p[v]"


def build_render_args(
    *,
    src: str,
    start_ms: int,
    duration_ms: int,
    filtergraph: str,
    has_audio: bool,
    preset: str,
    crf: int,
    audio_normalize: bool,
    output: str = "output.mp4",
) -> list[str]:
    dur = f"{duration_ms / 1000:.3f}"
    args = ["-ss", f"{start_ms / 1000:.3f}", "-t", dur, *input_opts(src), "-i", src, "-filter_complex", filtergraph, "-map", "[v]"]
    if has_audio:
        af = "aresample=async=1:first_pts=0"
        if audio_normalize:
            af += ",loudnorm=I=-14:TP=-1.5:LRA=11"
        args += ["-map", "0:a:0?", "-af", af, "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
    args += [
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high", "-level:v", "4.2",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn",
        "-t", dur, output,
    ]
    return args
