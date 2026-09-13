"""The worker's charge and refund SQL, against a real PostgreSQL.

These are the claims fakes cannot prove: that concurrent charges for one piece of
work debit exactly once, that a refund restores the buckets it came from, and
that one subject's key prefix never matches another's. Skipped when no database
is reachable (DATABASE_URL, else the local Compose default).

Runs against the development database, so it creates its own user and removes
it afterwards — and never uses a real-looking subject: idempotency keys are
unique across ALL users, so "video:7" would collide with an actual video 7.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import uuid

import pytest

from cliprover_worker.db import Database

pytestmark = pytest.mark.db

# 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first and Compose
# only publishes on IPv4, which costs ~4 s per connection.
DSN = os.environ.get("DATABASE_URL", "postgresql://cliprover:cliprover@127.0.0.1:5432/cliprover")


def unused_id() -> int:
    """An id far beyond any identity value a development database will reach."""
    return 10**15 + secrets.randbelow(10**14)


@pytest.fixture
async def ledger():
    db = Database(DSN)
    try:
        await asyncio.wait_for(db.open(), timeout=15)
    except Exception as exc:  # noqa: BLE001 - any connection failure means "no database here"
        await db.close()
        pytest.skip(f"PostgreSQL not reachable: {exc!r}")
    row = await db.fetchone(
        "INSERT INTO users (email, password_hash) VALUES (%s, 'test-only') RETURNING user_id",
        [f"ledger-test-{uuid.uuid4().hex[:10]}@example.com"],
    )
    user_id = str(row["user_id"])

    async def seed(rollover: int, expiring: int) -> None:
        await db.execute(
            "INSERT INTO credit_balances (user_id, rollover_credits, expiring_credits) VALUES (%s, %s, %s)",
            [user_id, rollover, expiring],
        )

    async def balance() -> tuple[int, int]:
        b = await db.fetchone("SELECT rollover_credits, expiring_credits FROM credit_balances WHERE user_id = %s", [user_id])
        return int(b["rollover_credits"]), int(b["expiring_credits"])

    async def keys() -> list[str]:
        rows = await db.fetchall(
            "SELECT idempotency_key FROM credit_ledger_entries WHERE user_id = %s ORDER BY credit_ledger_entry_id", [user_id]
        )
        return [r["idempotency_key"] for r in rows]

    try:
        yield db, user_id, seed, balance, keys
    finally:
        await db.execute("DELETE FROM credit_ledger_entries WHERE user_id = %s", [user_id])
        await db.execute("DELETE FROM credit_balances WHERE user_id = %s", [user_id])
        await db.execute("DELETE FROM users WHERE user_id = %s", [user_id])
        await db.close()


async def test_charge_refund_and_charge_again_across_retries(ledger):
    db, user, seed, balance, keys = ledger
    await seed(rollover=50, expiring=10)
    video = f"video:{unused_id()}"

    first = await db.charge_credits(user, video, 25, {})
    assert (first.outcome, first.available) == ("charged", 35)
    assert await balance() == (35, 0), "expiring credits are spent first"

    again = await db.charge_credits(user, video, 25, {})
    assert again.outcome == "already-charged"
    assert await balance() == (35, 0)

    assert await db.refund_credits(user, video, "SYSTEM_INTERNAL") is True
    assert await balance() == (50, 10), "a refund restores the buckets the charge came from"
    assert await db.refund_credits(user, video, "SYSTEM_INTERNAL") is False

    retried = await db.charge_credits(user, video, 25, {})
    assert retried.outcome == "charged"
    assert await keys() == [f"charge:{video}:1", f"refund:{video}:1", f"charge:{video}:2"]


async def test_a_short_balance_debits_nothing(ledger):
    db, user, seed, balance, keys = ledger
    await seed(rollover=3, expiring=2)

    result = await db.charge_credits(user, f"render:{unused_id()}", 6, {})
    assert (result.outcome, result.available) == ("insufficient", 5)
    assert await balance() == (3, 2)
    assert await keys() == []


async def test_concurrent_charges_for_the_same_work_debit_exactly_once(ledger):
    db, user, seed, balance, keys = ledger
    await seed(rollover=100, expiring=0)
    video = f"video:{unused_id()}"

    results = await asyncio.gather(*(db.charge_credits(user, video, 10, {}) for _ in range(6)))
    assert sorted(r.outcome for r in results) == ["already-charged"] * 5 + ["charged"]
    assert await balance() == (90, 0)
    assert await keys() == [f"charge:{video}:1"]


async def test_one_subjects_key_prefix_never_matches_another(ledger):
    db, user, seed, _balance, keys = ledger
    await seed(rollover=100, expiring=0)
    base = unused_id()
    short, longer = f"video:{base}", f"video:{base}0"

    # "charge:video:N:%" must not match "charge:video:N0:1".
    assert (await db.charge_credits(user, longer, 5, {})).outcome == "charged"
    assert (await db.charge_credits(user, short, 5, {})).outcome == "charged"
    assert await db.refund_credits(user, short, "X") is True
    assert await db.refund_credits(user, short, "X") is False
    assert await keys() == [f"charge:{longer}:1", f"charge:{short}:1", f"refund:{short}:1"]


async def test_the_first_charge_creates_a_missing_balance_row(ledger):
    db, user, _seed, balance, _keys = ledger
    result = await db.charge_credits(user, f"render:{unused_id()}", 1, {})
    assert (result.outcome, result.available) == ("insufficient", 0)
    assert await balance() == (0, 0)
