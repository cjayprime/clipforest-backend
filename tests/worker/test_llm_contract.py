"""Structured-output schema validation and repair paths for the Claude and OpenAI
providers, exercised against stub clients (no network, no cost)."""

from types import SimpleNamespace

import openai
import pydantic
import pytest

from cliprover_worker import errors
from cliprover_worker.analysis.chunking import Window
from cliprover_worker.analysis.llm import AnthropicLlm, OpenAiLlm, resolve_llm, resolve_llm_name
from cliprover_worker.analysis.models import LlmProposal, ModelScores, WindowProposals
from cliprover_worker.config import Settings
from cliprover_worker.transcription.base import Segment


def _validation_error() -> pydantic.ValidationError:
    try:
        WindowProposals.model_validate({"candidates": "not-a-list"})
    except pydantic.ValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")


GOOD = WindowProposals(
    candidates=[
        LlmProposal(
            start_segment="s0", end_segment="s1", title="T", hook_text="h", summary="s", reason="r", category="story",
            scores=ModelScores(hook=8, clarity=7, novelty=6, emotion=5, completeness=8, shareability=6),
        )
    ]
)
WINDOW = Window(0, 0, 10_000, [Segment("s0", 0, 4000, "Hello there."), Segment("s1", 4000, 9000, "This is a test.")])


class StubCalls:
    """Replays a scripted sequence of responses/exceptions and records the requests."""

    def __init__(self, behaviours):
        self.behaviours = list(behaviours)
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        b = self.behaviours.pop(0)
        if isinstance(b, Exception):
            raise b
        return b


# ------------------------------------------------------------------ Claude

def _anthropic_response(parsed, stop_reason="end_turn"):
    usage = SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0)
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason, usage=usage, _request_id="req_test")


def make_anthropic(monkeypatch, behaviours):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    llm = AnthropicLlm(Settings())
    stub = StubCalls(behaviours)
    llm.client = SimpleNamespace(beta=SimpleNamespace(messages=stub))
    return llm, stub


async def test_anthropic_request_shape(monkeypatch):
    llm, stub = make_anthropic(monkeypatch, [_anthropic_response(GOOD)])
    assert len(await llm.propose(WINDOW, 1, 10_000)) == 1
    call = stub.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_format"] is WindowProposals
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "[s0 |" in call["messages"][0]["content"]


async def test_anthropic_invalid_output_is_repaired(monkeypatch):
    llm, stub = make_anthropic(monkeypatch, [_validation_error(), _anthropic_response(GOOD)])
    assert len(await llm.propose(WINDOW, 1, 10_000)) == 1
    assert len(stub.calls) == 2 and "could not be used" in stub.calls[1]["messages"][0]["content"]


async def test_anthropic_truncation_is_retried(monkeypatch):
    llm, stub = make_anthropic(monkeypatch, [_anthropic_response(None, "max_tokens"), _anthropic_response(GOOD)])
    assert len(await llm.propose(WINDOW, 1, 10_000)) == 1
    assert len(stub.calls) == 2


async def test_anthropic_persistent_invalid_output_fails_the_stage(monkeypatch):
    llm, _ = make_anthropic(monkeypatch, [_validation_error(), _validation_error(), _validation_error()])
    with pytest.raises(errors.PipelineError) as info:
        await llm.propose(WINDOW, 1, 10_000)
    assert info.value.code == "ANALYSIS_INVALID_OUTPUT" and info.value.retryable


async def test_anthropic_refusal_yields_no_candidates(monkeypatch):
    llm, _ = make_anthropic(monkeypatch, [_anthropic_response(None, "refusal")])
    assert await llm.propose(WINDOW, 1, 10_000) == []


# ------------------------------------------------------------------ OpenAI

def _openai_response(parsed, refusal=None):
    message = SimpleNamespace(parsed=parsed, refusal=refusal)
    usage = SimpleNamespace(prompt_tokens=120, completion_tokens=60)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def make_openai(monkeypatch, behaviours, **env):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    llm = OpenAiLlm(Settings())
    stub = StubCalls(behaviours)
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=stub))
    return llm, stub


async def test_openai_request_shape(monkeypatch):
    llm, stub = make_openai(monkeypatch, [_openai_response(GOOD)])
    out = await llm.propose(WINDOW, 1, 10_000)
    assert len(out) == 1 and out[0].title == "T"
    call = stub.calls[0]
    assert call["model"] == "gpt-5.5"
    assert call["response_format"] is WindowProposals
    assert [m["role"] for m in call["messages"]] == ["system", "user"]
    assert "[s0 |" in call["messages"][1]["content"]
    assert "reasoning_effort" not in call


async def test_openai_sends_configured_effort(monkeypatch):
    llm, stub = make_openai(monkeypatch, [_openai_response(GOOD)], LLM_EFFORT="high", OPENAI_MODEL="gpt-5.4")
    await llm.propose(WINDOW, 1, 10_000)
    assert stub.calls[0]["reasoning_effort"] == "high" and stub.calls[0]["model"] == "gpt-5.4"


async def test_openai_drops_effort_when_the_model_rejects_it(monkeypatch):
    bad = openai.BadRequestError(
        "Unsupported parameter: 'reasoning_effort'", response=SimpleNamespace(status_code=400, headers={}, request=None), body=None
    )
    llm, stub = make_openai(monkeypatch, [bad, _openai_response(GOOD)], LLM_EFFORT="xhigh")
    assert len(await llm.propose(WINDOW, 1, 10_000)) == 1
    assert "reasoning_effort" in stub.calls[0] and "reasoning_effort" not in stub.calls[1]


async def test_openai_invalid_output_is_repaired(monkeypatch):
    llm, stub = make_openai(monkeypatch, [_validation_error(), _openai_response(GOOD)])
    assert len(await llm.propose(WINDOW, 1, 10_000)) == 1
    assert "could not be used" in stub.calls[1]["messages"][1]["content"]


async def test_openai_refusal_yields_no_candidates(monkeypatch):
    llm, _ = make_openai(monkeypatch, [_openai_response(None, refusal="I can't help with that")])
    assert await llm.propose(WINDOW, 1, 10_000) == []


async def test_openai_auth_error_is_non_retryable(monkeypatch):
    err = openai.AuthenticationError("bad key", response=SimpleNamespace(status_code=401, headers={}, request=None), body=None)
    llm, _ = make_openai(monkeypatch, [err])
    with pytest.raises(errors.PipelineError) as info:
        await llm.propose(WINDOW, 1, 10_000)
    assert info.value.code == "SYSTEM_MISCONFIGURED" and not info.value.retryable


async def test_openai_rate_limit_is_retryable(monkeypatch):
    err = openai.RateLimitError("slow down", response=SimpleNamespace(status_code=429, headers={}, request=None), body=None)
    llm, _ = make_openai(monkeypatch, [err])
    with pytest.raises(errors.PipelineError) as info:
        await llm.propose(WINDOW, 1, 10_000)
    assert info.value.code == "ANALYSIS_LLM_ERROR" and info.value.retryable


# -------------------------------------------------------------- resolution

def test_provider_follows_whichever_key_is_set(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    assert resolve_llm_name(Settings()) == "heuristic"
    assert resolve_llm(Settings()).name == "heuristic"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert resolve_llm_name(Settings()) == "openai"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert resolve_llm_name(Settings()) == "anthropic", "Anthropic wins when both keys are present"

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert resolve_llm_name(Settings()) == "openai", "an explicit provider overrides auto-detection"
