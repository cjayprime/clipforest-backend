import re

from clipforest_worker.captions.ass import PRESETS, ass_time, build_ass, escape, get_preset
from clipforest_worker.captions.grouping import clip_words, group_phrases
from clipforest_worker.transcription.base import Word
from helpers import words_for

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
