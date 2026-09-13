# Tests

Every backend suite lives here and runs against the code in `api/` and `worker/`. Browser tests belong to the frontend repo.

| Folder | Suite | Run it |
| --- | --- | --- |
| `api/` | API unit tests (state machines, idempotency keys, object keys, SSRF validation, render ranges) | `cd api && npm test` |
| `worker/` | Worker unit tests + FFmpeg/OpenCV integration tests over generated fixtures | host: `cd worker && .venv/Scripts/python -m pytest ../tests/worker -m "not ffmpeg"` · container (all): `cd infra && docker compose --env-file ../.env run --rm --no-deps worker python -m pytest -q /app/tests` |
| `e2e/` | API-level acceptance run against a live stack (PRD §22) | `python tests/e2e/golden_path.py --base http://127.0.0.1:4000 --video tests/.fixtures/sample.mp4` |
| `e2e/` | Billing acceptance run: Polar webhooks, the annual allowance job, credit charging — no Polar account needed | see [Billing](#billing) |

The FFmpeg-marked worker tests need `ffmpeg`/`ffprobe` on PATH, which is why they run in the worker container; `-m "not ffmpeg"` skips them on the host.

On Windows with Git Bash, prefix the container command with `MSYS_NO_PATHCONV=1` or `/app/tests` is rewritten into a Windows path.

## Prerequisites for the acceptance run

```bash
cd infra && docker compose --env-file ../.env up -d     # postgres, redis, minio, worker
cd api && npm run start:dev                             # API on :4000
```

With no provider keys set it runs fully offline (mock transcription + heuristic analysis) and still produces real MP4 clips.

## Fixtures

`tests/.fixtures/sample.mp4` (gitignored) is generated, not committed:

```bash
mkdir -p tests/.fixtures
docker run --rm -v "$PWD/tests/.fixtures:/out" cliprover-worker \
  ffmpeg -v error -y -f lavfi -i testsrc2=size=640x360:rate=24 -f lavfi -i sine=frequency=300 \
  -t 100 -c:v libx264 -preset veryfast -crf 38 -pix_fmt yuv420p -c:a aac -b:a 64k /out/sample.mp4
```

It is a synthetic test pattern with no faces, so renders correctly fall back to center crop — face tracking is covered by `worker/test_render.py` instead.

It also has **no speech**, only a test tone. The acceptance run therefore needs the mock transcription provider: with a real one the transcript is empty, analysis yields zero candidates (a valid outcome), and there is nothing to render. With real provider keys in `.env`, override them for the run rather than editing the file — shell variables outrank `--env-file`:

```bash
cd infra
TRANSCRIPTION_PROVIDER=mock LLM_PROVIDER=heuristic docker compose --env-file ../.env up -d worker
# ...run the acceptance script, then restore:
docker compose --env-file ../.env up -d worker
```

The worker's FFmpeg tests generate their own fixtures (landscape, portrait, no-audio, corrupt) into a temp directory on first run.

## Billing

`e2e/billing_e2e.py` exercises everything after Polar's HTTP call without ever contacting Polar: it signs webhook deliveries itself with the secret the API was started with, the way Polar does. It covers:

- signature checks, webhook redelivery, and ledger idempotency
- the rollover/expiring split and the SSE events
- the annual allowance job
- worker charging, including refusing work and letting the user retry after topping up

It needs an API with test billing settings, separate from the dev API so your `.env` is untouched, and a worker whose credit settings match it. The costs are deliberately high: a 60-credit minute makes the 100-second fixture cost more than one Starter grant, which is what the retry scenario needs.

```bash
SECRET="whsec_$(node -e "console.log(require('crypto').randomBytes(32).toString('base64'))")"

# 1. Worker: charging on, mock providers (the fixture has no speech), matching costs.
cd infra
TRANSCRIPTION_PROVIDER=mock LLM_PROVIDER=heuristic CREDITS_ENFORCED=true \
  CREDIT_COST_PER_SOURCE_MINUTE=60 CREDIT_COST_PER_RENDER=5 \
  docker compose --env-file ../.env up -d --build worker

# 2. A second API on :4100 (after `npm run build` in api/). The six product ids are
#    placeholders — the script only needs them to match; the cron runs every 5 seconds.
cd ../api
PORT=4100 POLAR_WEBHOOK_SECRET="$SECRET" \
  POLAR_PRODUCT_STARTER=e2e_starter_month POLAR_PRODUCT_STARTER_ANNUAL=e2e_starter_year \
  POLAR_PRODUCT_CREATOR=e2e_creator_month POLAR_PRODUCT_CREATOR_ANNUAL=e2e_creator_year \
  POLAR_PRODUCT_STUDIO=e2e_studio_month POLAR_PRODUCT_STUDIO_ANNUAL=e2e_studio_year \
  CREDITS_ENFORCED=true CREDIT_COST_PER_SOURCE_MINUTE=60 CREDIT_COST_PER_RENDER=5 \
  CREDIT_ANNUAL_ALLOWANCE_CRON='*/5 * * * * *' node dist/main.js &

# 3. Run it from the backend root (the worker venv has httpx).
cd ..
worker/.venv/Scripts/python tests/e2e/billing_e2e.py --base http://127.0.0.1:4100 \
  --secret "$SECRET" --video tests/.fixtures/sample.mp4

# 4. Stop the :4100 API and restore the worker.
cd infra && docker compose --env-file ../.env up -d worker
```

Omit `--video` to run only the webhook and annual sections, which need neither the fixture nor a worker. The run creates `billing-*@example.com` accounts in the local database.
