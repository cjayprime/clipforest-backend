"""Prompts for highlight discovery (PRD §7.3) and the functions that fill them in. The system prompts are stable per configuration so they can be served from the prompt cache across windows."""

from __future__ import annotations

from ..config import Settings
from .chunking import Window, fmt_ts, window_text
from .models import Candidate


PROPOSE_SYSTEM = """You are a senior short-form video editor. You find moments in long-form transcripts that can stand alone as TikTok, Instagram Reels or YouTube Shorts clips.

You receive one window of a longer transcript. Each line has the form:
[segment-id | start-end] (speaker) text

Choose moments that:
- are understandable with little or no context from outside the clip;
- contain at least one strong short-form signal: a hook or curiosity gap near the start; a surprising, contrarian or specific claim; clear advice, an explanation, a list, a story payoff or a lesson; emotional intensity, humor, tension or a memorable reaction expressed in the speech; high information density with little filler;
- reach a natural ending or conclusion;
- run roughly {min_s}-{max_s} seconds.

Rules:
- Return between 0 and {max_n} candidates. Return an empty list when nothing in the window is genuinely strong. Never pad the list with weak moments.
- Use only segment IDs that appear in this window. start_segment is the clip's first line and end_segment its last line (inclusive).
- Start at the beginning of a thought, not on a dangling "and/but/so" or a pronoun whose referent is outside the clip. End after a complete sentence, answer or punchline.
- Judge from the spoken words only; do not assume anything about the visuals.
- hook_text quotes the clip's opening line. Titles are specific, at most 60 characters, with no hashtags, emojis or promises the clip does not deliver.
- Score every dimension 0-10 using the full range: 9-10 exceptional, 7-8 strong, 5-6 average, 3-4 weak, 0-2 absent. Most moments are not exceptional.
- Candidates in the same window must not substantially overlap."""

PROPOSE_USER = """Transcript window {index} of {total} (covers {start}-{end} of a {duration} video):

{text}"""

RANK_SYSTEM = """You are ranking a shortlist of candidate short-form clips taken from the same long-form video.

Re-score every candidate on the same 0-10 scales (hook, clarity, novelty, emotion, completeness, shareability), comparing the candidates against each other so the scores are consistent across the whole list. Use the full range and be honest: most clips are not exceptional.

Judge only from the transcript text. Keep each title specific and at most 60 characters; change a title only if it misrepresents or clearly undersells the clip. Give a one-sentence reason. Return every candidate_id exactly once."""

RANK_USER = """Shortlist ({count} candidates):

{items}"""


def propose_system(s: Settings) -> str:
    return PROPOSE_SYSTEM.format(
        min_s=s.candidate_min_ms // 1000, max_s=s.candidate_max_ms // 1000, max_n=s.candidates_per_window
    )


def propose_user(window: Window, total_windows: int, duration_ms: int) -> str:
    return PROPOSE_USER.format(
        index=window.index + 1,
        total=total_windows,
        start=fmt_ts(window.start_ms),
        end=fmt_ts(window.end_ms),
        duration=fmt_ts(duration_ms),
        text=window_text(window),
    )


def rank_user(shortlist: list[Candidate]) -> str:
    items = "\n\n".join(
        f'<candidate id="{c.key}" duration="{c.duration_ms / 1000:.0f}s" category="{c.category}">\n'
        f"Title: {c.title}\nTranscript: {c.excerpt}\n</candidate>"
        for c in shortlist
    )
    return RANK_USER.format(count=len(shortlist), items=items)


def repair_suffix(user: str, last_error: str | None) -> str:
    """Re-prompt text appended when the previous answer failed schema validation."""
    if last_error is None:
        return user
    return f"{user}\n\nYour previous answer could not be used ({last_error}). Answer again, following the schema exactly."
