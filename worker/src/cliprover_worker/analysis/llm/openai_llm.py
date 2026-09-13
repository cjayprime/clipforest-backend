"""GPT via the OpenAI SDK, using schema-validated structured outputs."""

from __future__ import annotations

import openai
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
                    {"role": "user", "content": repair_suffix(user, last_error)},
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
            purpose="propose", system=propose_system(self.s), user=propose_user(window, total_windows, duration_ms), output_model=WindowProposals
        )
        return list(out.candidates) if out else []

    async def rank(self, shortlist: list[Candidate]) -> dict[str, LlmRankItem] | None:
        out = await self._parse(purpose="rank", system=RANK_SYSTEM, user=rank_user(shortlist), output_model=RankResult)
        return {item.candidate_id: item for item in out.candidates} if out else None
