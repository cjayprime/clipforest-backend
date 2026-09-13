# Billing — subscriptions and credits

Subscriptions are sold through [Polar](https://polar.sh). A subscription buys **credits** that land in the user's balance; processing and rendering spend them. The plan name is a label — nothing in the product checks which plan a user is on.

Card details never reach this API. Checkout and cancellation happen on Polar's hosted pages; the API holds a customer ID, a subscription row, a balance and a ledger.

## The two balances

Each user has two buckets on `credit_balances`:

| Bucket | Column | At a grant |
| --- | --- | --- |
| Rollover | `rollover_credits` | **Accumulates** — added to what is already there. |
| Expiring | `expiring_credits` | **Replaced** — the previous period's remainder is discarded. |

`CREDIT_ROLLOVER_SHARE` (default `0.9`) splits every grant when it is issued: 100 credits become 90 rollover + 10 expiring. Whole credits only, with the remainder in the expiring bucket, so rounding never creates credits (`api/src/billing/credits.ts`). Debits take from the expiring bucket first.

Every movement is a row in `credit_ledger_entries` (`grant`, `spend`, `refund`, `adjustment`) with a unique `idempotency_key`, and the balance row is locked `FOR UPDATE` while it changes.

## Credits in

### Paid orders (Polar webhook)

`POST /api/billing/webhooks/polar` (`polar/polar-webhook.controller.ts`):

1. Refuses every delivery when `POLAR_WEBHOOK_SECRET` is unset.
2. Verifies the Standard Webhooks signature over `{webhook-id}.{webhook-timestamp}.{raw body}` within `POLAR_WEBHOOK_TOLERANCE_SEC`. Both secret encodings are accepted — base64 after `whsec_`, and the legacy UTF-8 key (`polar/verify-webhook.ts`).
3. Ignores a `webhook-id` already recorded in `processed_webhooks`.
4. `order.paid` with billing reason `purchase` or `subscription_cycle` grants one month of the plan's credits under `polar-order:<order id>`. `subscription_update` (a mid-cycle plan change) grants nothing. The grant's metadata records `checkoutId`.
5. `subscription.*` events upsert the local `subscriptions` row.

A grant publishes `credits.updated`, and a subscription change `subscription.updated`, on the user's SSE channel after the write commits; the web app refetches the balance on either. `rawBody: true` in `main.ts` is required for signature verification.

### Annual subscriptions

Polar bills an annual plan once, so its order only grants month 0. `AnnualAllowanceScheduler` (`@nestjs/schedule`, `CREDIT_ANNUAL_ALLOWANCE_CRON`, default hourly) grants months 1–11 to active annual subscriptions as each monthly anniversary passes within the paid period, under `annual-allowance:<subscription>:<period start>:<month>`. Months that were already granted are skipped by key, so every API instance can run the job and a job that was down catches up.

## Credits out

Charging is off unless `CREDITS_ENFORCED=true`, which must be set on both the API and the worker.

| Work | Cost | Checked up front (API) | Debited (worker) |
| --- | --- | --- | --- |
| Processing a video | `CREDIT_COST_PER_SOURCE_MINUTE` × started minutes of source | Video create (one minute's cost); retry/re-analyse of a video that never reached READY (full cost when the duration is known) | Ingest, after the probe measures the duration |
| Rendering a clip | `CREDIT_COST_PER_RENDER` | Render create (after deduplication) and render retry | Render job start |

Re-analysing a video that already reached READY is free, and a deduplicated render costs nothing.

The worker keys each debit by subject — `video:<id>` or `render:<id>` — and numbers them: `charge:<subject>:1`, `refund:<subject>:1`, `charge:<subject>:2`, … A subject with more charges than refunds has an open charge, so a queue retry of the same job is not charged again (`db.py` `charge_credits` / `refund_credits`).

- **Refunds.** A render that fails is refunded. A video that fails before ever reaching READY is refunded; a failed re-analysis of a READY video is not. Retrying refunded work charges it again.
- **Insufficient credits.** The API answers `402 BILLING_INSUFFICIENT_CREDITS` before the work starts. If the balance drops between that check and the debit, the job fails with the same code, marked retryable for the user but not retried by the queue.

Debits and refunds publish `credits.updated` with status `SPENT` or `REFUNDED`.

## Checkout

`POST /api/billing/checkout` takes `{ plan, interval }` (`interval` defaults to `month`) and returns Polar's hosted checkout URL. Each plan has a monthly and an annual product (`POLAR_PRODUCT_<PLAN>` and `POLAR_PRODUCT_<PLAN>_ANNUAL`); `/api/config` reports `billingIntervals`, the intervals every paid plan has a product for.

Polar returns the customer to `/settings?checkout=<id>`. `GET /api/billing/checkout/:checkoutId` answers `pending` until a grant carrying that checkout id exists for the signed-in user, then `credited`.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /api/billing/balance` | Both buckets and the total, `rolloverShare`, plan, subscription status and period end, and the paid plan catalog. |
| `POST /api/billing/checkout` | Starts a Polar checkout for a plan and interval. `BILLING_PLAN_UNAVAILABLE` when that product is not configured. |
| `GET /api/billing/checkout/:checkoutId` | `pending` or `credited`, scoped to the signed-in user. |
| `POST /api/billing/portal` | Short-lived link to Polar's customer portal. `BILLING_NO_SUBSCRIPTION` when there is none. |
| `POST /api/billing/webhooks/polar` | Polar only; excluded from the OpenAPI contract. |

`/api/config` also serves `billingEnabled`, `creditRolloverShare` and `creditCosts` (`enforced`, `perSourceMinute`, `perRender`).

## Configuration

| Variable | Notes |
| --- | --- |
| `POLAR_ACCESS_TOKEN` | Unset: billing is off, checkout and portal answer `BILLING_NOT_CONFIGURED`, `billingEnabled` is false. |
| `POLAR_WEBHOOK_SECRET` | Unset: every webhook delivery is refused. |
| `POLAR_SERVER` / `POLAR_API_BASE` | `sandbox` or `production`; the API base defaults to `https://api.polar.sh` — set the sandbox host explicitly. |
| `POLAR_PRODUCT_STARTER` / `_CREATOR` / `_STUDIO` | Monthly product IDs. |
| `POLAR_PRODUCT_STARTER_ANNUAL` / `_CREATOR_ANNUAL` / `_STUDIO_ANNUAL` | Annual product IDs. |
| `POLAR_WEBHOOK_TOLERANCE_SEC` | Signature replay window, default 300. |
| `CREDIT_ROLLOVER_SHARE` | 0–1, default 0.9. |
| `CREDITS_ENFORCED` | Default `false`. API and worker. |
| `CREDIT_COST_PER_SOURCE_MINUTE` / `CREDIT_COST_PER_RENDER` | Default 1 / 1. API and worker. |
| `CREDIT_ANNUAL_ALLOWANCE_CRON` | Default `0 * * * *`; six-field expressions (with seconds) are accepted. |

The plan catalog (`plans.ts`) mirrors the pricing page. Its prices are for display only; the amount charged is whatever the Polar product is configured with.

## Verification

- **Unit tests** — signature verification (both encodings, tolerance, rotation), the split and grant arithmetic, product mapping and purchasable intervals, annual month scheduling, grant/subscription event ordering, processing cost (shared table with the worker), the worker's charge/refund helpers and retry semantics.
- **PostgreSQL tests** (`tests/worker/db`) — charge, refund and re-charge; a short balance; six concurrent charges debiting once; subject prefix isolation.
- **Acceptance** (`tests/e2e/billing_e2e.py`, no Polar account) — signed webhooks, redelivery and idempotency, the split, SSE events, checkout status, the annual job, and real worker debits including a refused video retried after topping up.
- **Not exercised** — calls to Polar itself: creating a checkout, the customer portal (its REST path is marked unconfirmed in `polar/polar.service.ts`), and real Polar webhook payloads.
