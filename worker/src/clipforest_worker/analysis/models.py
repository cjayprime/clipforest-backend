"""Candidate data contract (PRD §7.4) and the structured-output schemas sent to the LLM."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["insight", "story", "humor", "controversy", "advice", "reaction", "other"]
CATEGORIES = ("insight", "story", "humor", "controversy", "advice", "reaction", "other")
SCORE_KEYS = ("hook", "clarity", "novelty", "emotion", "completeness", "shareability")


class ModelScores(BaseModel):
    hook: int = Field(description="0-10. How quickly the moment earns attention in its first seconds.")
    clarity: int = Field(description="0-10. How understandable it is with no surrounding context.")
    novelty: int = Field(description="0-10. How specific, non-generic or surprising the content is.")
    emotion: int = Field(description="0-10. Humor, tension, surprise, excitement or empathy in the speech.")
    completeness: int = Field(description="0-10. Whether the clip resolves its own premise with a payoff.")
    shareability: int = Field(description="0-10. Likelihood someone would quote, save or send it.")


class LlmProposal(BaseModel):
    start_segment: str = Field(description="ID of the transcript line where the clip starts, e.g. 's12'.")
    end_segment: str = Field(description="ID of the transcript line where the clip ends (inclusive), e.g. 's19'.")
    title: str = Field(description="Short, specific title for the clip, at most 60 characters. No hashtags or emojis.")
    hook_text: str = Field(description="The opening line of the clip, quoted from the transcript.")
    summary: str = Field(description="One sentence describing what happens in the clip.")
    reason: str = Field(description="One sentence explaining why this moment works as a short.")
    category: Category
    scores: ModelScores


class WindowProposals(BaseModel):
    candidates: list[LlmProposal] = Field(description="Zero or more strong, self-contained moments. Empty if none qualify.")


class LlmRankItem(BaseModel):
    candidate_id: str
    title: str = Field(description="Improved title, at most 60 characters. Keep the original if it is already good.")
    reason: str = Field(description="One sentence explaining why this moment works as a short.")
    scores: ModelScores


class RankResult(BaseModel):
    candidates: list[LlmRankItem]


@dataclass
class Candidate:
    """A candidate in source time (integer milliseconds)."""

    start_ms: int
    end_ms: int
    title: str
    hook_text: str
    summary: str
    reason: str
    category: str
    scores: dict[str, int]
    score: int = 0
    excerpt: str = ""
    key: str = ""
    window: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_row(self, rank: int) -> dict:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "title": self.title[:120],
            "hook_text": self.hook_text[:500],
            "excerpt": self.excerpt[:1200],
            "summary": self.summary[:500],
            "reason": self.reason[:500],
            "category": self.category if self.category in CATEGORIES else "other",
            "score": self.score,
            "component_scores": dict(self.scores),
            "rank": rank,
        }
