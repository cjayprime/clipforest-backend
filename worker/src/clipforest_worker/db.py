"""PostgreSQL access. PostgreSQL is authoritative for all durable state (PRD §15).

Tables are defined by the TypeORM migrations in api/src/migrations; column names
are a cross-runtime contract, since this module reads them with raw SQL.
All writes are idempotent (conditional transitions, upserts, unique keys) so
BullMQ retries and stalled-job recovery never duplicate side effects.
"""

from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _clean_dsn(dsn: str) -> str:
    """Prisma DSNs may carry ?schema=...; libpq rejects unknown parameters."""
    parts = urlsplit(dsn)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in {"schema", "connection_limit", "pool_timeout"}]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _adapt(value: Any) -> Any:
    return Jsonb(value) if isinstance(value, (dict, list)) else value


def _set_clause(fields: dict[str, Any]) -> tuple[str, list[Any]]:
    cols, params = [], []
    for k, v in fields.items():
        if not _IDENT.match(k):
            raise ValueError(f"invalid column {k}")
        cols.append(f"{k} = %s")
        params.append(_adapt(v))
    return ", ".join(cols), params


class Database:
    def __init__(self, dsn: str):
        self.pool = AsyncConnectionPool(
            _clean_dsn(dsn), min_size=1, max_size=10, open=False, kwargs={"row_factory": dict_row, "autocommit": True}
        )

    async def open(self) -> None:
        await self.pool.open(wait=True, timeout=60)

    async def close(self) -> None:
        await self.pool.close()

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, list(params))
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, list(params))
            return await cur.fetchall()

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, list(params))
            return cur.rowcount

    # ---------------------------------------------------------------- videos
    async def get_video(self, video_id: str) -> dict | None:
        return await self.fetchone("SELECT * FROM videos WHERE id = %s", [video_id])

    async def update_video(self, video_id: str, **fields: Any) -> None:
        clause, params = _set_clause(fields)
        await self.execute(f"UPDATE videos SET {clause}, updated_at = now() WHERE id = %s", [*params, video_id])

    async def transition_video(self, video_id: str, to: str, from_states: Iterable[str], **fields: Any) -> bool:
        clause, params = _set_clause({"status": to, **fields})
        n = await self.execute(
            f"UPDATE videos SET {clause}, updated_at = now() WHERE id = %s AND deleted_at IS NULL AND status = ANY(%s)",
            [*params, video_id, list(from_states)],
        )
        return n == 1

    async def get_user_email(self, user_id: str) -> str | None:
        row = await self.fetchone("SELECT email FROM users WHERE id = %s", [user_id])
        return row["email"] if row else None

    # ----------------------------------------------------------- transcripts
    async def ensure_transcript(self, video_id: str, version: int, provider: str) -> dict:
        await self.execute(
            """INSERT INTO transcripts (video_id, version, provider, status, updated_at)
               VALUES (%s, %s, %s, 'PENDING', now()) ON CONFLICT (video_id, version) DO NOTHING""",
            [video_id, version, provider],
        )
        row = await self.fetchone("SELECT * FROM transcripts WHERE video_id = %s AND version = %s", [video_id, version])
        assert row is not None
        return row

    async def get_transcript(self, transcript_id: str) -> dict | None:
        return await self.fetchone("SELECT * FROM transcripts WHERE id = %s", [transcript_id])

    async def latest_transcript_version(self, video_id: str) -> int:
        row = await self.fetchone("SELECT max(version) AS v FROM transcripts WHERE video_id = %s", [video_id])
        return int(row["v"] or 0) if row else 0

    async def update_transcript(self, transcript_id: str, **fields: Any) -> None:
        clause, params = _set_clause(fields)
        await self.execute(f"UPDATE transcripts SET {clause}, updated_at = now() WHERE id = %s", [*params, transcript_id])

    # ------------------------------------------------------------ candidates
    async def replace_candidates(
        self,
        video_id: str,
        transcript_id: str,
        analysis_run: int,
        analysis_version: str,
        provider: str,
        candidates: list[dict],
    ) -> int:
        """Atomically publishes a run's candidates and supersedes older runs.

        Older candidates are soft-superseded (not deleted), so already-rendered
        clips keep their candidate reference (PRD §7.7).
        """
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM candidates WHERE video_id = %s AND analysis_run = %s", [video_id, analysis_run])
                await conn.execute(
                    "UPDATE candidates SET superseded_at = now() WHERE video_id = %s AND superseded_at IS NULL AND analysis_run <> %s",
                    [video_id, analysis_run],
                )
                for c in candidates:
                    await conn.execute(
                        """INSERT INTO candidates (video_id, transcript_id, start_ms, end_ms, title, hook_text, excerpt, summary,
                               reason, category, score, component_scores, analysis_version, analysis_run, provider, rank)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        [
                            video_id,
                            transcript_id,
                            c["start_ms"],
                            c["end_ms"],
                            c["title"],
                            c["hook_text"],
                            c["excerpt"],
                            c["summary"],
                            c["reason"],
                            c["category"],
                            c["score"],
                            Jsonb(c["component_scores"]),
                            analysis_version,
                            analysis_run,
                            provider,
                            c["rank"],
                        ],
                    )
        return len(candidates)

    # --------------------------------------------------------------- renders
    async def get_render(self, render_id: str) -> dict | None:
        return await self.fetchone("SELECT * FROM renders WHERE id = %s", [render_id])

    async def update_render(self, render_id: str, **fields: Any) -> None:
        clause, params = _set_clause(fields)
        await self.execute(f"UPDATE renders SET {clause}, updated_at = now() WHERE id = %s", [*params, render_id])

    async def transition_render(self, render_id: str, to: str, from_states: Iterable[str], **fields: Any) -> bool:
        clause, params = _set_clause({"status": to, **fields})
        n = await self.execute(
            f"UPDATE renders SET {clause}, updated_at = now() WHERE id = %s AND deleted_at IS NULL AND status = ANY(%s)",
            [*params, render_id, list(from_states)],
        )
        return n == 1

    # -------------------------------------------------------------- job runs
    async def job_started(self, queue: str, job_id: str, entity_type: str, entity_id: str, attempt: int, metadata: dict) -> None:
        await self.execute(
            """INSERT INTO job_runs (queue_name, external_job_id, entity_type, entity_id, attempt, status, started_at, metadata)
               VALUES (%s,%s,%s,%s,%s,'active',now(),%s)
               ON CONFLICT (queue_name, external_job_id, attempt)
               DO UPDATE SET status = 'active', started_at = now(), completed_at = NULL, error_code = NULL, error_message = NULL""",
            [queue, job_id, entity_type, entity_id, attempt, Jsonb(metadata)],
        )

    async def job_finished(
        self, queue: str, job_id: str, attempt: int, status: str, duration_ms: int, error_code: str | None = None, error_message: str | None = None
    ) -> None:
        await self.execute(
            """UPDATE job_runs SET status = %s, completed_at = now(), duration_ms = %s, error_code = %s, error_message = %s
               WHERE queue_name = %s AND external_job_id = %s AND attempt = %s""",
            [status, duration_ms, error_code, (error_message or "")[:2000] or None, queue, job_id, attempt],
        )

    # ----------------------------------------------------------------- usage
    async def record_usage(
        self, user_id: str, event_type: str, units: float, unit: str, idempotency_key: str, video_id: str | None = None, render_id: str | None = None
    ) -> None:
        await self.execute(
            """INSERT INTO usage_events (user_id, event_type, video_id, render_id, units, unit, idempotency_key)
               VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (idempotency_key) DO NOTHING""",
            [user_id, event_type, video_id, render_id, units, unit, idempotency_key],
        )

    # --------------------------------------------------------------- janitor
    async def stale_queued_videos(self, older_than_minutes: int = 10) -> list[dict]:
        return await self.fetchall(
            """SELECT id, pipeline_version, processing_run FROM videos
               WHERE status = 'QUEUED' AND deleted_at IS NULL AND updated_at < now() - make_interval(mins => %s) LIMIT 100""",
            [older_than_minutes],
        )

    async def stale_queued_renders(self, older_than_minutes: int = 15) -> list[dict]:
        return await self.fetchall(
            """SELECT id, video_id, attempt FROM renders
               WHERE status = 'QUEUED' AND deleted_at IS NULL AND updated_at < now() - make_interval(mins => %s) LIMIT 100""",
            [older_than_minutes],
        )

    async def unpurged_deleted_videos(self) -> list[dict]:
        return await self.fetchall(
            """SELECT id, user_id FROM videos WHERE deleted_at IS NOT NULL AND purged_at IS NULL
               AND deleted_at < now() - interval '2 minutes' LIMIT 50"""
        )

    async def deleted_render_outputs(self) -> list[dict]:
        return await self.fetchall(
            """SELECT r.id, r.user_id, r.video_id FROM renders r JOIN videos v ON v.id = r.video_id
               WHERE r.deleted_at IS NOT NULL AND v.deleted_at IS NULL AND r.output_key IS NOT NULL LIMIT 100"""
        )

    async def expired_render_outputs(self, days: int) -> list[dict]:
        return await self.fetchall(
            """SELECT id, user_id, video_id FROM renders
               WHERE output_key IS NOT NULL AND is_latest = false AND deleted_at IS NULL
               AND completed_at < now() - make_interval(days => %s) LIMIT 100""",
            [days],
        )

    async def expired_sources(self, days: int) -> list[dict]:
        return await self.fetchall(
            """SELECT id, user_id, object_key, proxy_key FROM videos
               WHERE deleted_at IS NULL AND source_expired_at IS NULL AND object_key IS NOT NULL
               AND created_at < now() - make_interval(days => %s) LIMIT 50""",
            [days],
        )

    async def delete_video_row(self, video_id: str) -> None:
        # Cascades to transcripts, candidates and renders (FK ON DELETE CASCADE).
        await self.execute("DELETE FROM videos WHERE id = %s AND deleted_at IS NOT NULL", [video_id])
