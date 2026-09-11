"""analysis: highlight discovery over the persisted transcript (PRD §7, progress 50-100%).

Re-running analysis never re-transcribes and never touches rendered clips; older
candidates are superseded, not deleted.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..analysis.pipeline import run_analysis
from ..errors import PipelineError
from ..metrics import ANALYSIS_EMPTY, CANDIDATES
from ..queues import ANALYSIS
from ..transcription.base import TranscriptResult
from .base import JobContext, JobHandler


class AnalysisHandler(JobHandler):
    queue = ANALYSIS

    async def run(self, ctx: JobContext) -> dict | None:
        s, db = self.s, self.db
        vid = ctx.data["videoId"]
        run_no = int(ctx.data["analysisRun"])
        v = await db.get_video(vid)
        if not v or v["deleted_at"]:
            return {"skipped": "deleted"}
        if v["status"] != "ANALYZING" or run_no != int(v["analysis_run"]):
            return {"skipped": "stale-or-finished"}

        row = await db.get_transcript(ctx.data["transcriptId"])
        if not row or row["status"] != "COMPLETED":
            raise PipelineError("ANALYSIS_NO_TRANSCRIPT", "The transcript is missing, so moments can't be found.", retryable=False)
        transcript = TranscriptResult.from_row(row)
        llm = self.svc.llm()

        await self.video_progress(v, 51, stage="analysis", substage="finding-moments", force=True)

        async def progress(frac: float) -> None:
            await self.video_progress(v, 50 + int(frac * 45), substage="finding-moments" if frac < 0.85 else "ranking")

        result = await run_analysis(transcript, int(v["duration_ms"]), llm, s, progress)
        rows = [c.to_row(rank=i + 1) for i, c in enumerate(result.candidates)]
        await db.replace_candidates(vid, str(row["id"]), run_no, s.analysis_version, f"{result.provider}:{result.model}", rows)
        CANDIDATES.inc(len(rows))
        if not rows:
            ANALYSIS_EMPTY.inc()

        if await db.transition_video(
            vid,
            "READY",
            ("ANALYZING",),
            progress=100,
            stage="ready",
            substage=f"{len(rows)} moments",
            ready_at=datetime.now(timezone.utc),
            error_code=None,
            error_message=None,
            error_retryable=None,
            failed_stage=None,
        ):
            v = await db.get_video(vid)
            await self.svc.events.video(v)
        return {"candidates": len(rows), "windows": result.windows, "proposals": result.proposals, "ranked": result.ranked}

    async def on_failure(self, ctx: JobContext, err: PipelineError) -> None:
        await self.fail_video(ctx.data["videoId"], err, ctx.correlation_id)

    async def on_retry(self, ctx: JobContext, err: PipelineError) -> None:
        await self.mark_video_retrying(ctx.data["videoId"], err)
