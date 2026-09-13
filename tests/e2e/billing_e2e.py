"""Billing acceptance run against a live stack — Polar webhooks, the annual
allowance job and credit charging — without a Polar account.

Polar is never contacted. This script signs webhook deliveries itself with the
secret the API was started with, exactly as Polar does (Standard Webhooks), so
everything after Polar's HTTP call runs for real: signature verification,
delivery dedupe, ledger idempotency, the rollover/expiring split, the annual
top-up job, SSE, and the worker's debits and their up-front refusal.

It needs an API started with test billing settings and a worker with matching
credit settings — see "Billing" in tests/README.md. Then, from the backend root:

    worker/.venv/Scripts/python tests/e2e/billing_e2e.py --base http://127.0.0.1:4100 \\
        --secret "$SECRET" --video tests/.fixtures/sample.mp4
"""

from __future__ import annotations

import argparse
import base64
import calendar
import hashlib
import hmac
import json
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

# Helpers shared with golden_path.py, which lives in the same directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from golden_path import check, poll, upload  # noqa: E402

PLAN_CREDITS = 100  # Starter, as advertised on the pricing page.
ROLLOVER, EXPIRING = 90, 10  # its 90/10 split at the default CREDIT_ROLLOVER_SHARE


# ----------------------------------------------------------------- helpers
def sign(secret: str, msg_id: str, timestamp: str, body: bytes) -> str:
    """Standard Webhooks v1: HMAC-SHA256 over "id.timestamp.body", keyed by the base64 after whsec_."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


class Polar:
    """Delivers webhooks the way Polar would."""

    def __init__(self, base: str, secret: str):
        self.url = f"{base}/api/billing/webhooks/polar"
        self.secret = secret

    def deliver(self, event_type: str, data: dict, *, msg_id: str | None = None, signed: bool = True, tamper: bool = False):
        msg_id = msg_id or f"msg_{uuid.uuid4().hex}"
        timestamp = str(int(time.time()))
        body = json.dumps({"type": event_type, "data": data}).encode()
        headers = {"content-type": "application/json", "webhook-id": msg_id, "webhook-timestamp": timestamp}
        if signed:
            headers["webhook-signature"] = sign(self.secret, msg_id, timestamp, body)
        if tamper:
            body = body.replace(b'"id": "', b'"id": "x', 1)  # altered after signing
        return httpx.post(self.url, content=body, headers=headers, timeout=30), msg_id


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def add_months(d: datetime, months: int) -> datetime:
    """Calendar months, clamped to a shorter month's last day (mirrors addCalendarMonths in the API)."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return d.replace(year=year, month=month, day=min(d.day, calendar.monthrange(year, month)[1]))


def subscription(sub_id: str, user_id: str, product: str, interval: str, start: datetime) -> dict:
    return {
        "id": sub_id,
        "status": "active",
        "recurring_interval": interval,
        "current_period_start": iso(start),
        "current_period_end": iso(add_months(start, 12 if interval == "year" else 1)),
        "cancel_at_period_end": False,
        "started_at": iso(start),
        "ends_at": None,
        "customer_id": f"cus_{user_id}",
        "product_id": product,
        "amount": 1500,
        "currency": "usd",
        "metadata": {"userId": user_id},
    }


def order(order_id: str, user_id: str, product: str, sub_id: str, reason: str) -> dict:
    return {
        "id": order_id,
        "status": "paid",
        "paid": True,
        "customer_id": f"cus_{user_id}",
        "product_id": product,
        "subscription_id": sub_id,
        "billing_reason": reason,
        "amount": 1500,
        "currency": "usd",
        "metadata": {"userId": user_id},
    }


def spend(balance: tuple[int, int], amount: int) -> tuple[int, int]:
    """Expected balance after a debit: expiring first (mirrors db.py charge_credits)."""
    rollover, expiring = balance
    from_expiring = min(expiring, amount)
    return rollover - (amount - from_expiring), expiring - from_expiring


class Account:
    def __init__(self, base: str, label: str):
        self.base = base
        self.c = httpx.Client(base_url=base, timeout=60)
        email = f"billing-{label}-{uuid.uuid4().hex[:8]}@example.com"
        r = self.c.post("/api/auth/register", json={"email": email, "password": "correct-horse-battery"})
        check(r.status_code == 201, f"registered account {label}")
        self.id = self.c.get("/api/auth/me").json()["user"]["id"]
        self.events: list[tuple[str, dict]] = []

    def balance(self) -> tuple[int, int]:
        credits = self.c.get("/api/billing/balance").json()["credits"]
        assert credits["total"] == credits["rollover"] + credits["expiring"], credits
        return credits["rollover"], credits["expiring"]

    def tap_events(self) -> None:
        """Collects this user's SSE events in the background, as the web app receives them."""

        def run() -> None:
            with httpx.Client(base_url=self.base, cookies=self.c.cookies, timeout=httpx.Timeout(10, read=None)) as sse:
                with sse.stream("GET", "/api/events") as s:
                    name = None
                    for line in s.iter_lines():
                        if line.startswith("event:"):
                            name = line[6:].strip()
                        elif line.startswith("data:") and name and name != "ping":
                            self.events.append((name, json.loads(line[5:])))

        threading.Thread(target=run, daemon=True).start()
        time.sleep(1.5)

    def saw(self, name: str, status: str | None = None, timeout: float = 10) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(n == name and (status is None or e.get("status") == status) for n, e in self.events):
                return True
            time.sleep(0.25)
        return False

    def wait_balance(self, expected: tuple[int, int], timeout: float) -> tuple[int, int]:
        deadline, current = time.time() + timeout, self.balance()
        while current != expected and time.time() < deadline:
            time.sleep(1)
            current = self.balance()
        return current

    def process_fixture(self, data: bytes) -> str:
        meta = {"sourceType": "upload", "originalFilename": "billing.mp4", "contentType": "video/mp4", "sizeBytes": len(data)}
        video = self.c.post("/api/videos", json={**meta, "rightsConfirmed": True}).json()
        parts = upload(self.c, video["id"], data)
        body = {"parts": parts} if parts else {"observedSizeBytes": len(data)}
        check(self.c.post(f"/api/videos/{video['id']}/upload-complete", json=body).status_code == 200, "upload completed")
        return video["id"]


def error_code(r: httpx.Response) -> str | None:
    try:
        return r.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------- sections
def webhooks(base: str, polar: Polar, product: str) -> Account:
    print("webhooks: signature, dedupe, idempotent grants, the two buckets, SSE")
    a = Account(base, "a")
    a.tap_events()
    sub_id = f"sub_{uuid.uuid4().hex[:10]}"

    r, _ = polar.deliver("order.paid", order("ord_unsigned", a.id, product, sub_id, "purchase"), signed=False)
    check(r.status_code == 401 and error_code(r) == "WEBHOOK_SIGNATURE_INVALID", "unsigned delivery refused")
    r, _ = polar.deliver("order.paid", order("ord_tampered", a.id, product, sub_id, "purchase"), tamper=True)
    check(r.status_code == 401, "delivery altered after signing refused")
    check(a.balance() == (0, 0), "refused deliveries granted nothing")

    r, _ = polar.deliver("subscription.created", subscription(sub_id, a.id, product, "month", datetime.now(timezone.utc)))
    check(r.status_code == 202, "subscription.created accepted")
    check(a.c.get("/api/billing/balance").json()["plan"] == "starter", "subscription projected as the starter plan")
    check(a.saw("subscription.updated"), "subscription.updated reached the user's SSE stream")

    checkout_id = f"chk_{uuid.uuid4().hex[:12]}"
    purchase = {**order(f"ord_{uuid.uuid4().hex[:10]}", a.id, product, sub_id, "purchase"), "checkout_id": checkout_id}

    def status(acct: Account) -> str:
        return acct.c.get(f"/api/billing/checkout/{checkout_id}").json()["status"]

    check(status(a) == "pending", "returning from checkout before the webhook: status pending")
    r, msg_id = polar.deliver("order.paid", purchase)
    check(r.status_code == 202 and r.json().get("outcome") == "granted", "first paid order granted")
    check(a.balance() == (ROLLOVER, EXPIRING), f"grant split {ROLLOVER} rollover / {EXPIRING} expiring")
    check(a.saw("credits.updated", "GRANTED"), "credits.updated reached the user's SSE stream")
    check(status(a) == "credited", "the checkout now reports credited")
    check(status(Account(base, "x")) == "pending", "another account cannot see that checkout as credited")
    r = a.c.get("/api/billing/checkout/%7BCHECKOUT_ID%7D")
    check(r.status_code == 404, "an unsubstituted {CHECKOUT_ID} placeholder is not a checkout")

    r, _ = polar.deliver("order.paid", purchase, msg_id=msg_id)
    check(r.status_code == 202 and r.json().get("ignored") == "duplicate", "redelivery with the same webhook-id ignored")
    r, _ = polar.deliver("order.paid", purchase)
    check(r.status_code == 202 and r.json().get("outcome") == "duplicate", "same order under a new webhook-id not granted twice")
    r, _ = polar.deliver("order.paid", order(f"ord_{uuid.uuid4().hex[:10]}", a.id, product, sub_id, "subscription_update"))
    check(r.status_code == 202 and "billing_reason" in str(r.json().get("ignored")), "mid-cycle plan change grants nothing")
    r, _ = polar.deliver("order.paid", order(f"ord_{uuid.uuid4().hex[:10]}", a.id, "prod_not_configured", sub_id, "purchase"))
    check(r.status_code == 202 and r.json().get("ignored") == "unmapped-product", "unknown product grants nothing")
    check(a.balance() == (ROLLOVER, EXPIRING), "balance unchanged by every ignored delivery")

    r, _ = polar.deliver("order.paid", order(f"ord_{uuid.uuid4().hex[:10]}", a.id, product, sub_id, "subscription_cycle"))
    check(r.status_code == 202 and r.json().get("outcome") == "granted", "renewal granted")
    check(a.balance() == (2 * ROLLOVER, EXPIRING), "renewal: rollover accumulates, expiring is replaced")
    return a


def annual(base: str, polar: Polar, product: str, wait: float) -> None:
    print("annual: month 0 from the order, months 1-3 from the scheduled job")
    b = Account(base, "b")
    sub_id = f"sub_{uuid.uuid4().hex[:10]}"
    # Started three months (and two days) ago, so exactly months 1, 2 and 3 are due.
    start = add_months(datetime.now(timezone.utc), -3) - timedelta(days=2)
    r, _ = polar.deliver("subscription.created", subscription(sub_id, b.id, product, "year", start))
    check(r.status_code == 202, "annual subscription accepted")
    r, _ = polar.deliver("order.paid", order(f"ord_{uuid.uuid4().hex[:10]}", b.id, product, sub_id, "purchase"))
    check(r.status_code == 202 and r.json().get("outcome") == "granted", "annual order granted month 0")
    # Four grants of 100: rollover stacks to 360, expiring is replaced each time.
    expected = (4 * ROLLOVER, EXPIRING)
    got = b.wait_balance(expected, wait)
    check(got == expected, f"scheduled job granted months 1-3 (balance {got}, expected {expected})")
    time.sleep(min(wait, 12))
    check(b.balance() == expected, "later runs of the job granted nothing again")


def charging(base: str, polar: Polar, product: str, a: Account, data: bytes, timeout: float) -> None:
    costs = httpx.get(f"{base}/api/config").json()["creditCosts"]
    if not costs["enforced"]:
        print("charging: SKIPPED — the API reports creditCosts.enforced=false")
        return
    per_minute, per_render = costs["perSourceMinute"], costs["perRender"]
    print(f"charging: {per_minute} credits/source minute, {per_render}/render")

    c = Account(base, "c")
    r = c.c.post("/api/videos", json={"sourceType": "upload", "originalFilename": "x.mp4", "contentType": "video/mp4", "sizeBytes": 10, "rightsConfirmed": True})
    check(r.status_code == 402 and error_code(r) == "BILLING_INSUFFICIENT_CREDITS", "no credits: refused before the upload")

    a.tap_events()
    before = a.balance()
    vid = a.process_fixture(data)
    ready = poll(a.c, f"/api/videos/{vid}", {"READY", "FAILED"}, timeout)
    check(ready["status"] == "READY", f"video processed (error={ready.get('error')})")
    cost = -(-ready["durationMs"] // 60_000) * per_minute
    after_video = spend(before, cost)
    check(a.wait_balance(after_video, 15) == after_video, f"worker debited {cost} credits, expiring bucket first -> {after_video}")
    check(a.saw("credits.updated", "SPENT"), "the worker's charge reached the user's SSE stream")

    cand = a.c.get(f"/api/videos/{vid}/candidates?minScore=0").json()["items"][0]
    settings = {"aspectRatio": "9:16", "framingMode": "center", "captions": {"enabled": False, "preset": "minimal"}}
    render = a.c.post(f"/api/candidates/{cand['id']}/renders", json=settings).json()
    done = poll(a.c, f"/api/renders/{render['id']}", {"COMPLETED", "FAILED"}, timeout)
    check(done["status"] == "COMPLETED", f"render completed (error={done.get('error')})")
    after_render = spend(after_video, per_render)
    check(a.wait_balance(after_render, 15) == after_render, f"render debited {per_render} -> {after_render}")
    dup = a.c.post(f"/api/candidates/{cand['id']}/renders", json=settings).json()
    check(dup["deduplicated"] is True, "asking for the same clip again is deduplicated")
    time.sleep(3)
    check(a.balance() == after_render, "a deduplicated render costs nothing")

    # The worker refuses what the API could not know: a video longer than the balance covers.
    if not (cost > PLAN_CREDITS >= per_minute):
        print(f"      (skipping the mid-pipeline refusal: needs cost {cost} > {PLAN_CREDITS} >= {per_minute}/min)")
        return
    d = Account(base, "d")
    sub_id = f"sub_{uuid.uuid4().hex[:10]}"
    polar.deliver("subscription.created", subscription(sub_id, d.id, product, "month", datetime.now(timezone.utc)))
    polar.deliver("order.paid", order(f"ord_{uuid.uuid4().hex[:10]}", d.id, product, sub_id, "purchase"))
    check(d.wait_balance((ROLLOVER, EXPIRING), 10) == (ROLLOVER, EXPIRING), f"account d has {PLAN_CREDITS} credits")
    vid = d.process_fixture(data)
    failed = poll(d.c, f"/api/videos/{vid}", {"READY", "FAILED"}, timeout)
    err = failed.get("error") or {}
    check(failed["status"] == "FAILED" and err.get("code") == "BILLING_INSUFFICIENT_CREDITS", f"worker refused a {cost}-credit video mid-pipeline")
    check(err.get("retryable") is True, "the failure is retryable by the user")
    check(d.balance() == (ROLLOVER, EXPIRING), "nothing was debited for the refused video")
    r = d.c.post(f"/api/videos/{vid}/process")
    check(r.status_code == 402 and error_code(r) == "BILLING_INSUFFICIENT_CREDITS", "retry refused up front while still short")
    time.sleep(3)
    check(d.c.get(f"/api/videos/{vid}").json()["status"] == "FAILED", "not retried automatically in the meantime")

    polar.deliver("order.paid", order(f"ord_{uuid.uuid4().hex[:10]}", d.id, product, sub_id, "subscription_cycle"))
    topped = (2 * ROLLOVER, EXPIRING)
    check(d.wait_balance(topped, 10) == topped, "account d topped up")
    r = d.c.post(f"/api/videos/{vid}/process")
    check(r.status_code == 202, "retry accepted after topping up")
    ready = poll(d.c, f"/api/videos/{vid}", {"READY", "FAILED"}, timeout)
    check(ready["status"] == "READY", f"retried video processed (error={ready.get('error')})")
    expected = spend(topped, cost)
    check(d.wait_balance(expected, 15) == expected, f"charged once, on the successful run -> {expected}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:4100")
    ap.add_argument("--secret", required=True, help="the POLAR_WEBHOOK_SECRET the API was started with")
    ap.add_argument("--product-monthly", default="e2e_starter_month")
    ap.add_argument("--product-annual", default="e2e_starter_year")
    ap.add_argument("--video", help="omit to skip the charging section")
    ap.add_argument("--allowance-wait", type=float, default=30, help="seconds to wait for the scheduled job")
    ap.add_argument("--timeout", type=float, default=900)
    args = ap.parse_args()
    started = time.time()
    polar = Polar(args.base, args.secret)

    cfg = httpx.get(f"{args.base}/api/config").json()
    check(cfg["billingIntervals"] == ["month", "year"], "API reports both billing intervals purchasable")

    a = webhooks(args.base, polar, args.product_monthly)
    annual(args.base, polar, args.product_annual, args.allowance_wait)
    if args.video:
        charging(args.base, polar, args.product_monthly, a, Path(args.video).read_bytes(), args.timeout)
    else:
        print("charging: SKIPPED — no --video given")
    print(json.dumps({"result": "PASS", "seconds": round(time.time() - started, 1)}, indent=2))


if __name__ == "__main__":
    main()
