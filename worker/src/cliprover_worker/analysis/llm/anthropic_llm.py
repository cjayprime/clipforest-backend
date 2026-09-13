"""Claude via the Anthropic SDK, using schema-validated structured outputs."""

from __future__ import annotations

import anthropic
import pydantic

from ... import errors
from ...config import Settings
from ...log import get_logger
from ...metrics import LLM_CALLS, LLM_TOKENS
from ..chunking import Window
from ..models import Candidate, LlmProposal, LlmRankItem, RankResult, WindowProposals
from ..prompts import RANK_SYSTEM
from ..prompts import propose_system, propose_user, rank_user, repair_suffix

log = get_logger(__name__)


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
                "messages": [{"role": "user", "content": repair_suffix(user, last_error)}],
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
            purpose="propose", system=propose_system(self.s), user=propose_user(window, total_windows, duration_ms), output_model=WindowProposals
        )
        return list(out.candidates) if out else []

    async def rank(self, shortlist: list[Candidate]) -> dict[str, LlmRankItem] | None:
        out = await self._parse(purpose="rank", system=RANK_SYSTEM, user=rank_user(shortlist), output_model=RankResult)
        return {item.candidate_id: item for item in out.candidates} if out else None
