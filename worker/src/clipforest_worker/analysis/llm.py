"""Language-model providers for highlight discovery (swappable, PRD §2.2).

  anthropic  Claude via the Anthropic SDK, schema-validated structured outputs
  openai     GPT via the OpenAI SDK, schema-validated structured outputs
  heuristic  deterministic text-signal scorer for local development without API keys

`LLM_PROVIDER=auto` picks whichever key is configured (Anthropic first, then
OpenAI) and otherwise falls back to the offline heuristic scorer.
"""

from __future__ import annotations

import os
import re
from typing import Protocol

import anthropic
import openai
import pydantic

from .. import errors
from ..config import Settings
from ..log import get_logger
from ..metrics import LLM_CALLS, LLM_TOKENS
from .chunking import Window, fmt_ts, window_text
from .models import Candidate, LlmProposal, LlmRankItem, ModelScores, RankResult, WindowProposals
from .prompts import PROPOSE_SYSTEM, PROPOSE_USER, RANK_SYSTEM, RANK_USER
from .scoring import aggregate

log = get_logger(__name__)


class LlmProvider(Protocol):
    name: str
    model: str

    async def propose(self, window: Window, total_windows: int, duration_ms: int) -> list[LlmProposal]: ...

    async def rank(self, shortlist: list[Candidate]) -> dict[str, LlmRankItem] | None: ...


def _propose_system(s: Settings) -> str:
    return PROPOSE_SYSTEM.format(
        min_s=s.candidate_min_ms // 1000, max_s=s.candidate_max_ms // 1000, max_n=s.candidates_per_window
    )


def _propose_user(window: Window, total_windows: int, duration_ms: int) -> str:
    return PROPOSE_USER.format(
        index=window.index + 1,
        total=total_windows,
        start=fmt_ts(window.start_ms),
        end=fmt_ts(window.end_ms),
        duration=fmt_ts(duration_ms),
        text=window_text(window),
    )


def _rank_user(shortlist: list[Candidate]) -> str:
    items = "\n\n".join(
        f'<candidate id="{c.key}" duration="{c.duration_ms / 1000:.0f}s" category="{c.category}">\n'
        f"Title: {c.title}\nTranscript: {c.excerpt}\n</candidate>"
        for c in shortlist
    )
    return RANK_USER.format(count=len(shortlist), items=items)


def _repair_suffix(user: str, last_error: str | None) -> str:
    if last_error is None:
        return user
    return f"{user}\n\nYour previous answer could not be used ({last_error}). Answer again, following the schema exactly."


class AnthropicLlm:
    name = "anthropic"

    def __init__(self, s: Settings):
        # The SDK retries connection errors, 408/409/429 and 5xx with exponential backoff.
        self.client = anthropic.AsyncAnthropic(max_retries=3, timeout=600.0)
        self.model = s.llm_model
        self.effort = s.llm_effort
        self.fallbacks = s.llm_refusal_fallbacks
        self.s = s

    async def _parse(self, *, purpose: str, system: str, user: str, output_model: type[pydantic.BaseModel]):
        last_error: str | None = None
        for _ in range(3):
            kwargs: dict = {
                "model": self.model,
                "max_tokens": 16000,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": _repair_suffix(user, last_error)}],
                "output_format": output_model,
            }
            if self.effort:
                kwargs["output_config"] = {"effort": self.effort}
            if self.fallbacks:
                # Server-side refusal fallback: a declined request is re-run on a fallback model in the same call.
                kwargs["betas"] = ["server-side-fallback-2026-07-01"]
                kwargs["fallbacks"] = "default"
            try:
                resp = await self.client.beta.messages.parse(**kwargs)
            except pydantic.ValidationError as exc:
                LLM_CALLS.labels(purpose, "invalid").inc()
                last_error = f"schema validation failed: {str(exc)[:300]}"
                continue
            except anthropic.AuthenticationError as exc:
                raise errors.misconfigured(f"Anthropic authentication failed: {exc.message}") from exc
            except anthropic.PermissionDeniedError as exc:
                raise errors.misconfigured(f"Anthropic permission denied: {exc.message}") from exc
            except anthropic.NotFoundError as exc:
                raise errors.misconfigured(f"LLM model {self.model!r} was not found") from exc
            except anthropic.BadRequestError as exc:
                if self.fallbacks and "fallback" in str(exc.message).lower():
                    log.warning("Refusal fallbacks unavailable; continuing without them")
                    self.fallbacks = False
                    continue
                LLM_CALLS.labels(purpose, "bad_request").inc()
                raise errors.analysis_failed(f"bad request: {exc.message}", retryable=False) from exc
            except anthropic.RateLimitError as exc:
                LLM_CALLS.labels(purpose, "rate_limited").inc()
                raise errors.analysis_failed("LLM rate limited", retryable=True) from exc
            except anthropic.APIConnectionError as exc:
                LLM_CALLS.labels(purpose, "connection").inc()
                raise errors.analysis_failed(f"LLM connection error: {exc}", retryable=True) from exc
            except anthropic.APIStatusError as exc:
                LLM_CALLS.labels(purpose, "error").inc()
                raise errors.analysis_failed(f"LLM error {exc.status_code}: {exc.message}", retryable=exc.status_code >= 500) from exc

            usage = resp.usage
            LLM_TOKENS.labels("input").inc(usage.input_tokens or 0)
            LLM_TOKENS.labels("output").inc(usage.output_tokens or 0)
            LLM_TOKENS.labels("cache_read").inc(getattr(usage, "cache_read_input_tokens", 0) or 0)
            if resp.stop_reason == "refusal":
                LLM_CALLS.labels(purpose, "refusal").inc()
                log.warning("LLM declined the request", extra={"purpose": purpose, "requestId": resp._request_id})
                return None
            if resp.stop_reason == "max_tokens":
                LLM_CALLS.labels(purpose, "truncated").inc()
                last_error = "the answer was truncated; return fewer and shorter items"
                continue
            parsed = resp.parsed_output
            if parsed is None:
                LLM_CALLS.labels(purpose, "invalid").inc()
                last_error = "no structured output was returned"
                continue
            LLM_CALLS.labels(purpose, "ok").inc()
            return parsed
        raise errors.analysis_invalid_output(last_error or "unknown")

    async def propose(self, window: Window, total_windows: int, duration_ms: int) -> list[LlmProposal]:
        out = await self._parse(
            purpose="propose", system=_propose_system(self.s), user=_propose_user(window, total_windows, duration_ms), output_model=WindowProposals
        )
        return list(out.candidates) if out else []

    async def rank(self, shortlist: list[Candidate]) -> dict[str, LlmRankItem] | None:
        out = await self._parse(purpose="rank", system=RANK_SYSTEM, user=_rank_user(shortlist), output_model=RankResult)
        return {item.candidate_id: item for item in out.candidates} if out else None


class OpenAiLlm:
    name = "openai"

    def __init__(self, s: Settings):
        # Reads OPENAI_API_KEY (and OPENAI_BASE_URL) from the environment.
        self.client = openai.AsyncOpenAI(max_retries=3, timeout=600.0, base_url=s.openai_base_url or None)
        self.model = s.openai_model
        self.effort = s.llm_effort
        self.s = s

    async def _parse(self, *, purpose: str, system: str, user: str, output_model: type[pydantic.BaseModel]):
        last_error: str | None = None
        send_effort = bool(self.effort)
        for _ in range(3):
            kwargs: dict = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": _repair_suffix(user, last_error)},
                ],
                "response_format": output_model,
            }
            if send_effort:
                kwargs["reasoning_effort"] = self.effort
            try:
                resp = await self.client.chat.completions.parse(**kwargs)
            except pydantic.ValidationError as exc:
                LLM_CALLS.labels(purpose, "invalid").inc()
                last_error = f"schema validation failed: {str(exc)[:300]}"
                continue
            except openai.LengthFinishReasonError:
                LLM_CALLS.labels(purpose, "truncated").inc()
                last_error = "the answer was truncated; return fewer and shorter items"
                continue
            except openai.ContentFilterFinishReasonError:
                LLM_CALLS.labels(purpose, "refusal").inc()
                log.warning("LLM declined the request", extra={"purpose": purpose})
                return None
            except openai.AuthenticationError as exc:
                raise errors.misconfigured(f"OpenAI authentication failed: {exc}") from exc
            except openai.PermissionDeniedError as exc:
                raise errors.misconfigured(f"OpenAI permission denied: {exc}") from exc
            except openai.NotFoundError as exc:
                raise errors.misconfigured(f"LLM model {self.model!r} was not found; set OPENAI_MODEL to a model your key can use") from exc
            except openai.BadRequestError as exc:
                # Some models reject reasoning_effort: drop it once and retry before failing.
                if send_effort and "reasoning" in str(exc).lower():
                    log.warning("Model rejected reasoning_effort; retrying without it", extra={"model": self.model})
                    send_effort = False
                    continue
                LLM_CALLS.labels(purpose, "bad_request").inc()
                raise errors.analysis_failed(f"bad request: {exc}", retryable=False) from exc
            except openai.RateLimitError as exc:
                LLM_CALLS.labels(purpose, "rate_limited").inc()
                raise errors.analysis_failed("LLM rate limited", retryable=True) from exc
            except openai.APIConnectionError as exc:
                LLM_CALLS.labels(purpose, "connection").inc()
                raise errors.analysis_failed(f"LLM connection error: {exc}", retryable=True) from exc
            except openai.APIStatusError as exc:
                LLM_CALLS.labels(purpose, "error").inc()
                raise errors.analysis_failed(f"LLM error {exc.status_code}", retryable=exc.status_code >= 500) from exc

            usage = getattr(resp, "usage", None)
            if usage:
                LLM_TOKENS.labels("input").inc(getattr(usage, "prompt_tokens", 0) or 0)
                LLM_TOKENS.labels("output").inc(getattr(usage, "completion_tokens", 0) or 0)
            message = resp.choices[0].message
            if getattr(message, "refusal", None):
                LLM_CALLS.labels(purpose, "refusal").inc()
                log.warning("LLM declined the request", extra={"purpose": purpose, "refusal": str(message.refusal)[:200]})
                return None
            parsed = message.parsed
            if parsed is None:
                LLM_CALLS.labels(purpose, "invalid").inc()
                last_error = "no structured output was returned"
                continue
            LLM_CALLS.labels(purpose, "ok").inc()
            return parsed
        raise errors.analysis_invalid_output(last_error or "unknown")

    async def propose(self, window: Window, total_windows: int, duration_ms: int) -> list[LlmProposal]:
        out = await self._parse(
            purpose="propose", system=_propose_system(self.s), user=_propose_user(window, total_windows, duration_ms), output_model=WindowProposals
        )
        return list(out.candidates) if out else []

    async def rank(self, shortlist: list[Candidate]) -> dict[str, LlmRankItem] | None:
        out = await self._parse(purpose="rank", system=RANK_SYSTEM, user=_rank_user(shortlist), output_model=RankResult)
        return {item.candidate_id: item for item in out.candidates} if out else None


# --------------------------------------------------------------- heuristic

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


def resolve_llm_name(s: Settings) -> str:
    """LLM_PROVIDER, or auto-detection from whichever API key is configured."""
    if s.llm_provider and s.llm_provider != "auto":
        return s.llm_provider
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "heuristic"


def resolve_llm(s: Settings) -> LlmProvider:
    name = resolve_llm_name(s)
    if name == "anthropic":
        return AnthropicLlm(s)
    if name == "openai":
        return OpenAiLlm(s)
    if name == "heuristic":
        return HeuristicLlm(s)
    raise errors.misconfigured(f"unknown LLM provider {name!r}")
