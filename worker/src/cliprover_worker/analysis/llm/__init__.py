"""Language-model providers for highlight discovery (PRD §2.2).

  anthropic  Claude via the Anthropic SDK, schema-validated structured outputs
  openai     GPT via the OpenAI SDK, schema-validated structured outputs
  heuristic  deterministic text-signal scorer for local development without API keys

`LLM_PROVIDER=auto` picks whichever key is configured (Anthropic first, then
OpenAI) and otherwise falls back to the offline heuristic scorer.
"""

from __future__ import annotations

from typing import Protocol
import os

from ..chunking import Window
from ..models import Candidate, LlmProposal, LlmRankItem
from ... import errors
from ...config import Settings
from .anthropic_llm import AnthropicLlm
from .heuristic_llm import HeuristicLlm
from .openai_llm import OpenAiLlm


class LlmProvider(Protocol):
    name: str
    model: str

    async def propose(self, window: Window, total_windows: int, duration_ms: int) -> list[LlmProposal]: ...

    async def rank(self, shortlist: list[Candidate]) -> dict[str, LlmRankItem] | None: ...


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
