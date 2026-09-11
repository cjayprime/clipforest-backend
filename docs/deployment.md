# Deployment — backend (single 8 GB Hetzner host)

## Topology

One Docker Compose project (`infra/docker-compose.yml`). PostgreSQL 16, Redis 7 (AOF, `noeviction`) and the Python worker always run; the `apps` profile adds the NestJS API and Caddy (TLS, reverse proxy) for the production host. External: Cloudflare R2, a hosted transcription API, the Anthropic or OpenAI API. Memory limits: worker 3 GB, Postgres 1 GB, API 512 MB, Redis 256 MB. Render concurrency is fixed at 1.

**The web app is not part of this deployment.** It is a separate repository with its own host, and reaches this one over HTTPS at whatever `SITE_ADDRESS` resolves to — so give this host an API-only name such as `api.clips.example.com`. Caddy here serves `/api/*` and 404s everything else; the frontend's own server forwards `/api` to it (see that repo's `docs/deployment.md`).

(For local development the same file runs only infrastructure plus the worker, and the API runs natively — see the README.)

## 1. Server

```bash
# Ubuntu 24.04, 8 GB RAM, ≥ 80 GB disk
apt update && apt install -y ca-certificates curl ufw
curl -fsSL https://get.docker.com | sh
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
git clone <repo> /opt/clipforest && cd /opt/clipforest
```

Point DNS (A/AAAA) for your domain at the server.

## 2. Cloudflare R2

1. Create a bucket (e.g. `clipforest`) and an R2 API token with Object Read & Write on it.
2. CORS (bucket → Settings → CORS policy) — required for browser uploads; `ETag` must be exposed for multipart:

The origin here is the **web app's** origin — that is where the browser uploads from — not this API host.

```json
[
  {
    "AllowedOrigins": ["https://clips.example.com"],
    "AllowedMethods": ["GET", "PUT", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

3. Lifecycle: add a rule to abort incomplete multipart uploads after 1 day. Output/source retention is handled by the worker (`RENDER_RETENTION_DAYS`, `SOURCE_RETENTION_DAYS`).

## 3. Environment

```bash
cp .env.example .env
```

Set at least:

| Variable | Production value |
| --- | --- |
| `COMPOSE_PROFILES` | `apps` (API + Caddy in Docker; no MinIO — storage is R2) |
| `SITE_ADDRESS` | `api.clips.example.com` (Caddy obtains certificates automatically) |
| `HTTP_PORT` / `HTTPS_PORT` | `80` / `443` |
| `PUBLIC_WEB_URL` | `https://clips.example.com` — the **web app's** origin, used for CORS |
| `DATABASE_URL` / `REDIS_URL` | only read when the API runs natively; ignore on a Compose host |
| `JWT_SECRET` | `openssl rand -hex 32` |
| `COOKIE_SECURE` | `true` |
| `POSTGRES_PASSWORD` | strong random |
| `S3_ENDPOINT_INTERNAL`, `S3_PUBLIC_ENDPOINT` | `https://<account>.r2.cloudflarestorage.com` |
| `S3_REGION` / `S3_FORCE_PATH_STYLE` | `auto` / `false` |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET` | from the R2 token |
| `ASSEMBLYAI_API_KEY` (or Deepgram/Whisper) | hosted transcription |
| `ANTHROPIC_API_KEY` | highlight analysis |
| `METRICS_TOKEN` | if scraping `/api/metrics` from outside the host |

Secrets live only in `.env` (mode 600) or your secret store — never in git.

## 4. Start / upgrade

```bash
cd infra
docker compose --env-file ../.env up -d --build
docker compose ps
curl -fsS https://api.clips.example.com/api/health/ready
```

Then deploy the web app from its repository with `API_INTERNAL_URL=https://api.clips.example.com`. The two sides upgrade independently; the contract between them is `contracts/openapi.json`.

The API applies pending TypeORM migrations on start (`DB_MIGRATIONS_RUN=false` to disable and run `npm run migration:run` separately). To upgrade: `git pull && docker compose --env-file ../.env up -d --build`. The worker finishes active jobs on `SIGTERM` (Compose default 10 s grace; raise `stop_grace_period` for long renders) and interrupted jobs are reclaimed by BullMQ and resumed idempotently.

## 5. Backups

- PostgreSQL (authoritative state): nightly logical dump, keep 14 days, copy off-host (e.g. to a separate R2 bucket).

```bash
# /etc/cron.d/clipforest-backup
15 3 * * * root docker exec clipforest-postgres-1 pg_dump -U clipforest -Fc clipforest > /var/backups/clipforest-$(date +\%F).dump && find /var/backups -name 'clipforest-*.dump' -mtime +14 -delete
```

  Restore: `docker exec -i clipforest-postgres-1 pg_restore -U clipforest -d clipforest --clean < file.dump`.
- Redis holds queues only (AOF enabled); after a Redis loss the janitor re-enqueues `QUEUED` work, and videos stuck mid-stage can be retried from the UI.
- R2 objects are durable; enable bucket versioning if you need accidental-delete protection.

## 6. Monitoring

- `GET /api/health` (liveness) and `/api/health/ready` (DB, Redis, storage).
- Prometheus: API `http://api:4000/api/metrics` (HTTP latency, queue depth by state, renders created/deduplicated — Caddy 404s this path from outside), worker `http://worker:9100/metrics` (jobs by outcome, stage durations, render seconds per output minute, transcription latency/minutes, LLM calls/tokens, candidates, framing strategy, temp-disk high-water and free bytes).
- Alert on: `clipforest_temp_disk_free_bytes < DISK_MIN_FREE_BYTES * 1.5`, sustained `clipforest_queue_jobs{state="waiting",queue="render"}` growth, `clipforest_worker_jobs_total{outcome="failed"}` rate.
- Logs are JSON on stdout with `correlationId`, `videoId`, `renderId`, `queueName`, `jobId`, `attempt`, `pipelineVersion`; no tokens or signed URLs are logged. `docker compose logs -f worker`.

## 7. Scaling out

PostgreSQL and Redis stay the coordination layer. Add render capacity by running the worker image on more hosts with the same `DATABASE_URL`, `REDIS_URL`, `S3_*` and `WORKER_QUEUES=render` (and `CONCURRENCY_RENDER=1` per 4 vCPUs). Keep `video-ingest,transcription,analysis,cleanup` on the main host or a separate worker. Expose Postgres/Redis to those hosts over a private network only.

## Optional: local transcription

`TRANSCRIPTION_PROVIDER=local-whisper` uses faster-whisper on CPU/int8. Install `requirements-local-whisper.txt` in the worker image, set `CONCURRENCY_TRANSCRIPTION=1`, and prefer a separate host: on the shared 8 GB box it is serialized with renders (in-process gate) but still competes for RAM.
