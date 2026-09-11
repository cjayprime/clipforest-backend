from clipforest_worker.analysis.dedupe import dedupe, iou
from clipforest_worker.analysis.models import Candidate
from clipforest_worker.analysis.scoring import WEIGHTS, aggregate


def cand(start, end, score, completeness=5):
    scores = {k: 5 for k in WEIGHTS}
    scores["completeness"] = completeness
    return Candidate(start, end, "t", "h", "s", "r", "insight", scores, score=score)


def test_weights_match_prd():
    assert WEIGHTS == {"hook": 0.25, "clarity": 0.20, "novelty": 0.15, "emotion": 0.15, "completeness": 0.15, "shareability": 0.10}
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_aggregate_is_deterministic_and_bounded():
    assert aggregate({k: 10 for k in WEIGHTS}) == 100
    assert aggregate({k: 0 for k in WEIGHTS}) == 0
    assert aggregate({"hook": 10}) == 25
    assert aggregate({"hook": 15, "clarity": -3}) == 25  # clamped to 0-10
    assert aggregate({"hook": 8, "clarity": 7, "novelty": 6, "emotion": 5, "completeness": 9, "shareability": 4}) == 68


def test_iou():
    assert iou(0, 10, 0, 10) == 1.0
    assert iou(0, 10, 10, 20) == 0.0
    assert abs(iou(0, 10, 5, 15) - 5 / 15) < 1e-9


def test_dedupe_keeps_stronger_candidate():
    a, b, c = cand(0, 30_000, 80), cand(3_000, 32_000, 60), cand(60_000, 90_000, 50)
    kept = dedupe([b, a, c], 0.5, 90_000)
    assert kept == [a, c]


def test_dedupe_expands_when_duplicate_is_more_complete():
    strong = cand(10_000, 40_000, 80, completeness=4)
    complete = cand(12_000, 50_000, 70, completeness=9)
    kept = dedupe([strong, complete], 0.5, 90_000)
    assert len(kept) == 1
    assert (kept[0].start_ms, kept[0].end_ms) == (10_000, 50_000)


def test_dedupe_never_expands_beyond_max():
    strong = cand(0, 60_000, 80, completeness=4)
    complete = cand(10_000, 75_000, 70, completeness=9)
    kept = dedupe([strong, complete], 0.5, 70_000)
    assert (kept[0].start_ms, kept[0].end_ms) == (0, 60_000)
