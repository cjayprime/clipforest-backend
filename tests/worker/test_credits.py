"""Credit charging in the worker: cost, retry semantics, and when charges and
refunds happen. The SQL itself is covered against PostgreSQL in test_credit_ledger_db.py."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from bullmq.custom_errors import UnrecoverableError

from cliprover_worker import errors
from cliprover_worker.config import Settings
from cliprover_worker.credits import ChargeResult, processing_cost
from cliprover_worker.errors import PipelineError
from cliprover_worker.jobs.base import JobHandler, make_processor
from cliprover_worker.jobs.render import RenderHandler

# Same cases as the processingCost table in tests/api/billing.spec.ts: the API
# refuses work with the TypeScript copy and the worker charges with this one.
COST_CASES = [
    (0, 1, 0),
    (1, 1, 1),
    (60_000, 1, 1),
    (60_001, 1, 2),
    (150_000, 2, 6),
    (100_000, 60, 120),
    (10_800_000, 1, 180),
    (-5, 1, 0),
    (60_000, 0, 0),
]


@pytest.mark.parametrize(("duration_ms", "per_minute", "expected"), COST_CASES)
def test_processing_cost_is_per_started_minute(duration_ms, per_minute, expected):
    assert processing_cost(duration_ms, per_minute) == expected


# ------------------------------------------------------------ retry semantics
def test_auto_retry_follows_retryable_for_every_existing_error():
    assert PipelineError("X", "m", retryable=True).auto_retry is True
    assert PipelineError("X", "m", retryable=False).auto_retry is False
    assert errors.internal("boom").auto_retry is True
    assert errors.video_too_long(60).auto_retry is False


def test_insufficient_credits_is_for_the_user_to_retry_not_the_queue():
    err = errors.insufficient_credits(120, 100)
    assert (err.code, err.retryable, err.auto_retry) == ("BILLING_INSUFFICIENT_CREDITS", True, False)
    assert err.details == {"needed": 120, "available": 100}


class _JobDb:
    def __init__(self):
        self.finished: list[str] = []

    async def job_started(self, *_args):
        return None

    async def job_finished(self, _queue, _job_id, _attempt, status, *_rest):
        self.finished.append(status)


class _FailingHandler(JobHandler):
    queue = "video-ingest"

    def __init__(self, svc, err: PipelineError):
        super().__init__(svc)
        self.err = err
        self.calls: list[str] = []

    async def run(self, ctx):
        raise self.err

    async def on_failure(self, ctx, err):
        self.calls.append("failure")

    async def on_retry(self, ctx, err):
        self.calls.append("retry")


def _job(attempts_made: int = 0, attempts: int = 3):
    return SimpleNamespace(data={"videoId": "1"}, attemptsMade=attempts_made, opts={"attempts": attempts}, id="job-1", name="t")


def _processor(tmp_path, err: PipelineError):
    db = _JobDb()
    svc = SimpleNamespace(settings=replace(Settings(), tmp_root=str(tmp_path)), db=db)
    handler = _FailingHandler(svc, err)
    return make_processor(handler, svc), handler, db


async def test_an_insufficient_credit_failure_fails_at_once_without_queue_retries(tmp_path):
    process, handler, db = _processor(tmp_path, errors.insufficient_credits(120, 100))
    with pytest.raises(UnrecoverableError):
        await process(_job(attempts_made=0, attempts=3), "token")
    assert handler.calls == ["failure"]
    assert db.finished == ["failed"]


async def test_a_transient_failure_is_still_retried_by_the_queue(tmp_path):
    process, handler, db = _processor(tmp_path, errors.internal("boom"))
    with pytest.raises(RuntimeError):
        await process(_job(attempts_made=0, attempts=3), "token")
    assert handler.calls == ["retry"]
    assert db.finished == ["retrying"]


# ------------------------------------------------------ charging and refunds
class _CreditDb:
    def __init__(self, outcome: str = "charged", available: int = 50, refunded: bool = True):
        self.outcome, self.available, self.refunded = outcome, available, refunded
        self.charges: list[tuple] = []
        self.refunds: list[tuple] = []
        self.video: dict | None = None
        self.render: dict | None = None

    async def charge_credits(self, user_id, subject, amount, metadata):
        self.charges.append((user_id, subject, amount))
        return ChargeResult(self.outcome, self.available)

    async def refund_credits(self, user_id, subject, reason):
        self.refunds.append((user_id, subject, reason))
        return self.refunded

    async def get_video(self, _video_id):
        return self.video

    async def transition_video(self, *_args, **_fields):
        return True

    async def get_render(self, _render_id):
        return self.render

    async def transition_render(self, *_args, **_fields):
        return True


class _Events:
    def __init__(self):
        self.sent: list[tuple] = []

    async def credits(self, user_id, status):
        self.sent.append(("credits", user_id, status))

    async def video(self, _video):
        self.sent.append(("video",))

    async def render(self, _render):
        self.sent.append(("render",))


class _Handler(RenderHandler):
    queue = "render"


def _handler(db: _CreditDb, *, enforced: bool = True, per_minute: int = 2) -> tuple[_Handler, _Events]:
    events = _Events()
    settings = replace(Settings(), credits_enforced=enforced, credit_cost_per_source_minute=per_minute)
    return _Handler(SimpleNamespace(settings=settings, db=db, events=events)), events


VIDEO = {"video_id": "9", "user_id": "7", "duration_ms": 150_000}


async def test_nothing_is_charged_while_charging_is_switched_off():
    db = _CreditDb()
    handler, events = _handler(db, enforced=False)
    await handler.charge_video(VIDEO)
    assert db.charges == [] and events.sent == []


async def test_a_video_is_charged_per_started_minute_and_announced():
    db = _CreditDb("charged")
    handler, events = _handler(db, per_minute=2)
    await handler.charge_video(VIDEO)
    assert db.charges == [("7", "video:9", 6)]
    assert events.sent == [("credits", "7", "SPENT")]


async def test_a_retried_job_finds_its_open_charge_and_announces_nothing():
    db = _CreditDb("already-charged")
    handler, events = _handler(db)
    await handler.charge_video(VIDEO)
    assert events.sent == []


async def test_a_short_balance_stops_the_job_with_the_amounts():
    handler, events = _handler(_CreditDb("insufficient", available=4), per_minute=2)
    with pytest.raises(PipelineError) as raised:
        await handler.charge_video(VIDEO)
    assert raised.value.code == "BILLING_INSUFFICIENT_CREDITS"
    assert raised.value.details == {"needed": 6, "available": 4}
    assert events.sent == []


async def test_free_work_never_touches_the_ledger():
    db = _CreditDb()
    handler, _ = _handler(db)
    await handler.charge("7", "render:1", 0, {})
    assert db.charges == []


async def test_refunds_run_even_with_charging_off_and_announce_only_real_refunds():
    db = _CreditDb(refunded=True)
    handler, events = _handler(db, enforced=False)
    await handler.refund("7", "video:9", "X")
    assert events.sent == [("credits", "7", "REFUNDED")]

    db.refunded = False
    events.sent.clear()
    await handler.refund("7", "video:9", "X")
    assert events.sent == []


def _video_row(ready_at):
    return {"video_id": "9", "user_id": "7", "status": "TRANSCRIBING", "deleted_at": None, "ready_at": ready_at}


async def test_a_video_that_never_became_ready_is_refunded_when_it_fails():
    db = _CreditDb()
    db.video = _video_row(ready_at=None)
    handler, _ = _handler(db)
    await handler.fail_video("9", errors.internal("boom"), "cid")
    assert db.refunds == [("7", "video:9", "SYSTEM_INTERNAL")]


async def test_a_failed_reanalysis_of_a_ready_video_keeps_its_charge():
    db = _CreditDb()
    db.video = {**_video_row(ready_at=datetime.now(timezone.utc)), "status": "ANALYZING"}
    handler, _ = _handler(db)
    await handler.fail_video("9", errors.internal("boom"), "cid")
    assert db.refunds == []


async def test_a_failed_render_is_always_refunded():
    db = _CreditDb()
    db.render = {"render_id": "5", "user_id": "7", "video_id": "9", "status": "RENDERING", "deleted_at": None}
    handler, _ = _handler(db)
    await handler.fail_render("5", errors.render_ffmpeg_failed("x"), "cid")
    assert db.refunds == [("7", "render:5", "RENDER_FFMPEG_FAILED")]
