"""Caption phrasing and ASS subtitle generation for burned-in, word-timed captions."""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from .transcription.base import Word


FILLERS = {"um", "uh", "erm", "er", "uhm", "hmm", "mm"}
SENTENCE_END = (".", "!", "?", "…")
_BARE = re.compile(r"[^\w']+")


@dataclass
class CaptionWord:
    start_ms: int
    end_ms: int
    text: str


@dataclass
class Phrase:
    words: list[CaptionWord]
    lines: list[list[CaptionWord]] = field(default_factory=list)
    start_ms: int = 0
    end_ms: int = 0

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def _bare(text: str) -> str:
    return _BARE.sub("", text).lower()


def clip_words(words: list[Word], clip_start_ms: int, clip_end_ms: int, remove_fillers: bool = True) -> list[CaptionWord]:
    """Selects words spoken inside the clip, converts to clip-local time and applies
    meaning-preserving cleanup (filler tokens, immediate stutter repeats)."""
    length = clip_end_ms - clip_start_ms
    out: list[CaptionWord] = []
    for w in words:
        if w.end_ms <= clip_start_ms or w.start_ms >= clip_end_ms:
            continue
        text = w.text.strip()
        if not text:
            continue
        bare = _bare(text)
        if remove_fillers and bare in FILLERS:
            continue
        if remove_fillers and out and bare and bare == _bare(out[-1].text) and w.start_ms - clip_start_ms - out[-1].end_ms < 400:
            out[-1].end_ms = max(out[-1].end_ms, min(length, w.end_ms - clip_start_ms))
            continue
        start = max(0, w.start_ms - clip_start_ms)
        end = min(length, w.end_ms - clip_start_ms)
        if end <= start:
            end = min(length, start + 1)
        out.append(CaptionWord(start, end, text))
    return out


def _balance_lines(words: list[CaptionWord], max_chars: int) -> list[list[CaptionWord]]:
    """One line if it fits; otherwise the most balanced two-line split (avoiding a lone word)."""
    total = len(" ".join(w.text for w in words))
    if total <= max_chars or len(words) < 2:
        return [words]
    best, best_cost = None, None
    for k in range(1, len(words)):
        a = len(" ".join(w.text for w in words[:k]))
        b = len(" ".join(w.text for w in words[k:]))
        cost = max(a, b) + (100 if max(a, b) > max_chars else 0) + (8 if (k == 1 or k == len(words) - 1) and len(words) > 2 else 0)
        if best_cost is None or cost < best_cost:
            best, best_cost = k, cost
    assert best is not None
    return [words[:best], words[best:]]


def group_phrases(
    words: list[CaptionWord],
    *,
    max_chars_per_line: int,
    max_words: int,
    max_lines: int = 2,
    gap_ms: int = 600,
    hold_ms: int = 300,
    clip_length_ms: int | None = None,
) -> list[Phrase]:
    phrases: list[Phrase] = []
    cur: list[CaptionWord] = []

    def flush():
        nonlocal cur
        if cur:
            phrases.append(Phrase(cur))
            cur = []

    for w in words:
        if cur:
            gap = w.start_ms - cur[-1].end_ms
            chars = len(" ".join(x.text for x in cur)) + 1 + len(w.text)
            if gap > gap_ms or len(cur) >= max_words or chars > max_chars_per_line * max_lines:
                flush()
        cur.append(w)
        if w.text.endswith(SENTENCE_END) and len(cur) >= 2:
            flush()
    flush()

    # Avoid a lone short word as its own caption when it can join the previous phrase.
    merged: list[Phrase] = []
    for p in phrases:
        if (
            merged
            and len(p.words) == 1
            and len(p.words[0].text) <= 5
            and len(merged[-1].words) < max_words + 1
            and p.words[0].start_ms - merged[-1].words[-1].end_ms <= gap_ms
            and len(merged[-1].text) + 1 + len(p.words[0].text) <= max_chars_per_line * max_lines
            and not merged[-1].words[-1].text.endswith(SENTENCE_END)
        ):
            merged[-1].words.append(p.words[0])
        else:
            merged.append(p)

    for i, p in enumerate(merged):
        p.lines = _balance_lines(p.words, max_chars_per_line)
        p.start_ms = p.words[0].start_ms  # never before the first word is spoken
        next_start = merged[i + 1].words[0].start_ms if i + 1 < len(merged) else None
        end = p.words[-1].end_ms + hold_ms
        if next_start is not None:
            end = min(end, next_start)
        if clip_length_ms is not None:
            end = min(end, clip_length_ms)
        p.end_ms = max(end, p.words[-1].end_ms if next_start is None else min(p.words[-1].end_ms, next_start), p.start_ms + 40)
    return merged


# Preset sizes and margins are expressed on this reference frame and scaled to the output.
REFERENCE_W, REFERENCE_H = 1080, 1920
SAFE_MARGIN_X = 90


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


def margin_x(width: int) -> int:
    """Left/right safe margin for a frame `width` pixels wide."""
    return round(SAFE_MARGIN_X * width / REFERENCE_W)


def line_chars(preset: CaptionPreset, width: int) -> int:
    """Characters per caption line for a frame `width` pixels wide."""
    usable = width - 2 * margin_x(width)
    return max(preset.max_chars, round(preset.max_chars * usable / (REFERENCE_W - 2 * SAFE_MARGIN_X)))


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
    usable = width - 2 * margin_x(width)
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
            str(margin_x(width)), str(margin_x(width)), str(round(preset.margin_v * height / REFERENCE_H)),
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
