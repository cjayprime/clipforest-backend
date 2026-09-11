from clipforest_worker.analysis.refine import RefineConfig, refine
from helpers import build_transcript

SENTENCES = [
    "Welcome back to the show everyone.",
    "And that is exactly why we changed everything last year.",
    "The biggest mistake we made was hiring way too fast.",
    "We learned it the hard way over two long years",
    "and it nearly cost us the entire company.",
    "Here is what we do differently now.",
    "Every hire needs a written scorecard before the first interview.",
    "Um so that is the rule we follow.",
    "It sounds simple but it changed everything for us.",
]
SEGS, WORDS = build_transcript(SENTENCES)
DURATION = SEGS[-1].end_ms + 2000


def cfg(min_ms=5_000, max_ms=60_000):
    return RefineConfig(min_ms, max_ms, 250, 350)


def seg_start(i):
    return SEGS[i].start_ms


def test_snaps_start_to_segment_beginning_without_leaking_previous_words():
    s, e = refine(seg_start(2) + 1500, SEGS[2].end_ms - 500, SEGS, WORDS, cfg(), DURATION)
    assert s <= seg_start(2)
    prev_word_end = max(w.end_ms for w in WORDS if w.end_ms <= seg_start(2))
    assert s >= prev_word_end


def test_pulls_in_previous_line_for_dangling_conjunction():
    s, _ = refine(seg_start(1), SEGS[1].end_ms, SEGS, WORDS, cfg(), DURATION)
    assert s <= seg_start(0) + 1  # "And ..." pulls in the prior line


def test_extends_end_to_complete_sentence():
    _, e = refine(seg_start(2), SEGS[3].end_ms - 200, SEGS, WORDS, cfg(), DURATION)
    assert e >= SEGS[4].end_ms  # segment 3 has no terminal punctuation; segment 4 completes it


def test_enforces_minimum_duration():
    s, e = refine(seg_start(2), SEGS[2].end_ms, SEGS, WORDS, cfg(min_ms=12_000), DURATION)
    assert e - s >= 12_000


def test_enforces_maximum_duration_on_sentence_boundary():
    s, e = refine(seg_start(2), SEGS[8].end_ms, SEGS, WORDS, cfg(max_ms=15_000), DURATION)
    assert e - s <= 15_000
    assert any(abs(e - (seg.end_ms + 350)) <= 400 or abs(e - seg.end_ms) <= 400 for seg in SEGS if seg.text.endswith("."))


def test_strips_leading_filler():
    s, _ = refine(seg_start(7), SEGS[8].end_ms, SEGS, WORDS, cfg(min_ms=3_000), DURATION)
    first = next(w for w in WORDS if w.start_ms >= seg_start(7))
    assert first.text == "Um"
    assert s > first.start_ms - 250 + 1  # starts after the filler word (minus pre-roll)


def test_results_stay_within_source_duration():
    s, e = refine(0, DURATION, SEGS, WORDS, cfg(max_ms=10 * 60_000), DURATION)
    assert 0 <= s < e <= DURATION
