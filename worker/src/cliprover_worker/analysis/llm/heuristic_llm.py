"""Deterministic offline scorer: keeps the whole pipeline runnable with no API keys."""

from __future__ import annotations

import re

from ...config import Settings
from ..chunking import Window
from ..models import Candidate, LlmProposal, LlmRankItem, ModelScores
from ..scoring import aggregate

_HOOK = {
    "secret", "mistake", "biggest", "nobody", "never", "always", "truth", "why", "how", "here's", "heres", "worst",
    "best", "lesson", "remember", "wrong", "rule", "controversial", "actually", "overrated", "wish", "changed", "interesting",
}
_EMOTION = {"laughed", "crazy", "insane", "shock", "love", "hate", "scared", "amazing", "honestly", "wow", "fear", "cried", "angry", "night"}
_WORD = re.compile(r"[A-Za-z0-9$%']+")


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text)]


class HeuristicLlm:
    """Deterministic, keyword/structure-based scorer. Not a quality model; it keeps the
    full pipeline runnable offline and in tests."""

    name = "heuristic"
    model = "heuristic-v1"

    def __init__(self, s: Settings):
        self.s = s

    def _scores(self, first: str, span: str, ends_cleanly: bool) -> dict[str, int]:
        ft, st = _tokens(first), _tokens(span)
        hook = min(10, 3 + 2 * sum(t in _HOOK for t in ft) + (2 if "?" in first else 0) + (1 if any(c.isdigit() for c in first) else 0))
        numbers = sum(any(c.isdigit() for c in t) for t in st) + sum(t in {"percent", "dollars", "million", "thousand", "hundred"} for t in st)
        filler = sum(t in {"um", "uh", "like", "yeah", "kind"} for t in st) / max(1, len(st))
        emotion = min(10, 3 + 2 * sum(t in _EMOTION for t in st) + span.count("!"))
        clarity = max(2, min(10, 8 - (2 if ft and ft[0] in {"and", "but", "so", "it", "that", "this"} else 0) - int(filler * 40)))
        novelty = min(10, 3 + min(5, numbers) + (1 if "controversial" in st or "overrated" in st else 0))
        completeness = 8 if ends_cleanly else 4
        share = min(10, (hook + novelty + emotion) // 3 + 1)
        return {"hook": hook, "clarity": clarity, "novelty": novelty, "emotion": emotion, "completeness": completeness, "shareability": share}

    async def propose(self, window: Window, total_windows: int, duration_ms: int) -> list[LlmProposal]:
        segs = window.segments
        target = min(self.s.candidate_max_ms, max(self.s.candidate_min_ms, 40_000))
        scored: list[tuple[int, int, int, LlmProposal]] = []
        for i, seg in enumerate(segs):
            first_tokens = _tokens(seg.text)
            signal = sum(t in _HOOK for t in first_tokens) + (2 if "?" in seg.text else 0) + (1 if any(c.isdigit() for c in seg.text) else 0)
            if signal < 2 or (first_tokens and first_tokens[0] in {"and", "but", "so"}):
                continue
            j = i
            while j + 1 < len(segs) and segs[j].end_ms - seg.start_ms < target:
                j += 1
            span = " ".join(s.text for s in segs[i : j + 1])
            ends = segs[j].text.rstrip().endswith((".", "!", "?"))
            scores = self._scores(seg.text, span, ends)
            if aggregate(scores) < 45:
                continue
            words = seg.text.split()
            title = " ".join(words[:9]).rstrip(".,!?") + ("…" if len(words) > 9 else "")
            proposal = LlmProposal(
                start_segment=seg.id,
                end_segment=segs[j].id,
                title=title[:60],
                hook_text=seg.text,
                summary=f"A {((segs[j].end_ms - seg.start_ms) / 1000):.0f}-second passage that opens with a strong line.",
                reason="Opens with a clear hook signal and resolves within the target length.",
                category="advice" if "rule" in span.lower() or "should" in span.lower() else "insight",
                scores=ModelScores(**scores),
            )
            scored.append((aggregate(scores), seg.start_ms, segs[j].end_ms, proposal))
        picked: list[tuple[int, int, int, LlmProposal]] = []
        for item in sorted(scored, key=lambda x: -x[0]):
            if all(item[2] <= p[1] or item[1] >= p[2] for p in picked):
                picked.append(item)
            if len(picked) >= self.s.candidates_per_window:
                break
        return [p[3] for p in picked]

    async def rank(self, shortlist: list[Candidate]) -> dict[str, LlmRankItem] | None:
        return None  # first-pass scores are already consistent for a deterministic scorer
