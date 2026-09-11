"""ASS subtitle generation with active-word emphasis (PRD §10.3).

Each phrase is emitted as consecutive events, one per spoken word, in which the
current word is highlighted — so emphasis advances monotonically with word timings.
Layout stays inside mobile-safe margins; lines that would overflow are scaled down.
"""

from __future__ import annotations

from dataclasses import dataclass

from .grouping import Phrase

SAFE_MARGIN_X = 90  # px on a 1080-wide frame


@dataclass(frozen=True)
class CaptionPreset:
    name: str
    font: str
    size: int
    bold: bool
    uppercase: bool
    primary: str
    outline_color: str
    shadow_color: str
    outline: float
    shadow: float
    emphasis_color: str | None
    emphasis_scale: int
    margin_v: int
    max_chars: int
    max_words: int
    char_width: float  # average glyph width as a fraction of font size (for overflow estimation)


PRESETS: dict[str, CaptionPreset] = {
    "bold-default": CaptionPreset("bold-default", "Inter", 80, True, False, "#FFFFFF", "#000000", "#000000", 6, 2, "#FFD60A", 108, 470, 17, 5, 0.62),
    "karaoke": CaptionPreset("karaoke", "Inter", 76, True, False, "#FFFFFF", "#0B1020", "#000000", 5, 1.5, "#4CD7F6", 104, 470, 18, 6, 0.6),
    "minimal": CaptionPreset("minimal", "DejaVu Sans", 62, False, False, "#FFFFFF", "#000000", "#000000", 3, 0, None, 100, 420, 24, 8, 0.58),
    "impact": CaptionPreset("impact", "Inter", 88, True, True, "#FFFFFF", "#000000", "#000000", 7, 2, "#4EDEA3", 112, 480, 13, 4, 0.72),
}


def get_preset(name: str) -> CaptionPreset:
    return PRESETS.get(name, PRESETS["bold-default"])


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (style colour)."""
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_tag_color(hex_rgb: str) -> str:
    """#RRGGBB -> &HBBGGRR& (override tag colour)."""
    h = hex_rgb.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def ass_time(ms: int) -> str:
    cs = max(0, int(round(ms / 10)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _display(text: str, preset: CaptionPreset) -> str:
    t = escape(text)
    return t.upper() if preset.uppercase else t


def _fit_scale(phrase: Phrase, preset: CaptionPreset, width: int) -> int:
    usable = width - 2 * SAFE_MARGIN_X
    longest = max(len(" ".join(w.text for w in line)) for line in phrase.lines)
    est = longest * preset.size * preset.char_width
    if preset.emphasis_color:
        est *= 1 + (preset.emphasis_scale - 100) / 100 / max(1, len(phrase.words))
    return 100 if est <= usable else max(55, int(usable / est * 100))


def _render_text(phrase: Phrase, preset: CaptionPreset, active: int | None, scale: int = 100) -> str:
    parts: list[str] = []
    idx = 0
    for li, line in enumerate(phrase.lines):
        tokens = []
        for w in line:
            word = _display(w.text, preset)
            if active is not None and idx == active and preset.emphasis_color:
                s = scale * preset.emphasis_scale // 100
                # Restore colour/scale explicitly (not \r) so a phrase-level scale-down survives.
                restore = f"{{\\c{ass_tag_color(preset.primary)}\\fscx{scale}\\fscy{scale}}}"
                tokens.append(f"{{\\c{ass_tag_color(preset.emphasis_color)}\\fscx{s}\\fscy{s}}}{word}{restore}")
            else:
                tokens.append(word)
            idx += 1
        parts.append(" ".join(tokens))
        if li < len(phrase.lines) - 1:
            parts.append("\\N")
    return "".join(parts)


def build_ass(phrases: list[Phrase], preset: CaptionPreset, width: int = 1080, height: int = 1920) -> str:
    style = ",".join(
        [
            "Default",
            preset.font,
            str(preset.size),
            ass_color(preset.primary),
            ass_color(preset.primary),
            ass_color(preset.outline_color),
            ass_color(preset.shadow_color, 0x60),
            "-1" if preset.bold else "0",
            "0", "0", "0",
            "100", "100", "0", "0",
            "1",
            f"{preset.outline:g}",
            f"{preset.shadow:g}",
            "2",
            str(SAFE_MARGIN_X), str(SAFE_MARGIN_X), str(preset.margin_v),
            "1",
        ]
    )
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: {style}",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def event(start: int, end: int, text: str) -> str:
        return f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}"

    for p in phrases:
        scale = _fit_scale(p, preset, width)
        prefix = f"{{\\fscx{scale}\\fscy{scale}}}" if scale < 100 else ""
        if not preset.emphasis_color:
            lines.append(event(p.start_ms, p.end_ms, prefix + _render_text(p, preset, None, scale)))
            continue
        for i, w in enumerate(p.words):
            start = p.start_ms if i == 0 else w.start_ms
            end = p.words[i + 1].start_ms if i + 1 < len(p.words) else p.end_ms
            if end <= start:
                continue
            lines.append(event(start, end, prefix + _render_text(p, preset, i, scale)))
    return "\n".join(lines) + "\n"
