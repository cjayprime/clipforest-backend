# ClipForest — backend

The server half of an AI short-form video clipping platform: ingest a long video, transcribe it, find the self-contained moments worth clipping, and render any of them as a captioned, subject-aware **1080×1920 H.264/AAC** MP4. Built to the PRD in [docs/AI_Short_Form_Video_Clipping_Product_Requirements.pdf](docs/AI_Short_Form_Video_Clipping_Product_Requirements.pdf).

The web app lives in its own repository and deploys independently — it only ever talks to this API over HTTP.

```
web app (separate repo) ──▶ NestJS API ──▶ PostgreSQL (authoritative state)
   │                            │  └──────▶ Redis/BullMQ (queues + progress pub/sub)
   │  signed PUT/GET            ▼
   └──────────────▶ Cloudflare R2 ◀── Python media worker (FFmpeg, OpenCV, providers)
```

Media never transits the API: the browser uploads to storage with signed URLs, and the worker reads it back the same way.

## Layout

| Path | What it is |
| --- | --- |
| `api/` | **Product API.** NestJS 11 + TypeORM 0.3 (PostgreSQL). Auth, direct-upload signing, videos, candidates, renders, SSE, metrics, OpenAPI. |
| `worker/` | **Media worker.** Python 3.12 BullMQ consumer: ingest, transcription, highlight analysis, smart-reframe rendering, cleanup. |
| `contracts/` | Queue payload JSON Schema, job-ID conventions, generated `openapi.json` — what the two runtimes and the frontend agree on. |
| `infra/` | Docker Compose for the single-host deployment, Caddy reverse proxy/TLS. |
| `docs/` | `pipeline.md`, `deployment.md`, `runbook.md`, the PRD. |
| `tests/` | Every suite — see [tests/README.md](tests/README.md). |

## Quick start

Docker runs only what needs it — PostgreSQL, Redis, S3-compatible storage and the Python media worker (FFmpeg, OpenCV). The API runs natively.

```bash
cp .env.example .env                                            # works as-is; add API keys for real providers
cd infra && docker compose --env-file ../.env up -d --build      # postgres, redis, minio, worker
cd ../api && npm install && npm run start:dev                    # API on :4000
```

Then start the web app from the frontend repo (`npm run dev`, :3000) and create an account — **no user is seeded, and nothing sends email**; registration is immediate.

`.env` is the single environment file for this repo. Compose reads it via `--env-file`, and the API reads the same file through `dotenv` when it runs natively. Host-side values (`DATABASE_URL`, `S3_ENDPOINT`) and container-side values (`POSTGRES_*`, `S3_ENDPOINT_INTERNAL`) use different names, so one file is correct for both.

The API applies pending TypeORM migrations at boot. For the production shape (API + Caddy in containers, TLS) use `COMPOSE_PROFILES=apps docker compose --env-file ../.env up -d --build`.

Useful URLs: API `:4000/api`, Swagger `:4000/api/docs`, MinIO console `127.0.0.1:9001`, worker metrics on `:9100` inside the container.

## Providers

Without API keys the stack runs fully offline: transcription uses the deterministic **mock** provider and highlight analysis uses the **heuristic** scorer, so the whole pipeline (upload → transcript → ranked moments → rendered MP4) works end to end without paid calls.

With `TRANSCRIPTION_PROVIDER=auto` and `LLM_PROVIDER=auto`, whichever key is set wins:

| Key | Used for |
| --- | --- |
| `ASSEMBLYAI_API_KEY` / `DEEPGRAM_API_KEY` / `WHISPER_API_KEY` | transcription (first one configured wins) |
| `ANTHROPIC_API_KEY` | highlight analysis with Claude (`LLM_MODEL`, default `claude-opus-5`) |
| `OPENAI_API_KEY` | highlight analysis with GPT (`OPENAI_MODEL`, default `gpt-5.5`) — used when no Anthropic key is set |

Force one with `LLM_PROVIDER=anthropic|openai|heuristic`. Storage is S3-compatible: MinIO locally, Cloudflare R2 in production, same signed-URL code path.

## Working on the worker natively

Docker is the supported way to run the worker (it carries FFmpeg, OpenCV and the fonts). To iterate natively you need FFmpeg on PATH:

```bash
cd worker && python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
PYTHONPATH=src DATABASE_URL=... REDIS_URL=... S3_ENDPOINT=http://localhost:9000 python -m clipforest_worker.main
```

## Database changes

Entities live in `api/src/entities`; the schema is owned by checked-in migrations in `api/src/migrations`. The Python worker reads the same tables with raw SQL, so **column names are a cross-runtime contract**. After changing an entity: `npm run build && npm run migration:generate`, review the SQL, then restart the API (or `npm run migration:run`).

## Tests

See [tests/README.md](tests/README.md) for prerequisites and fixtures.

```bash
cd api && npm test                                                     # tests/api  — unit
cd worker && .venv/Scripts/python -m pytest ../tests/worker -m "not ffmpeg"   # tests/worker — unit
cd infra && docker compose --env-file ../.env run --rm --no-deps worker python -m pytest -q /app/tests   # + FFmpeg/OpenCV
python tests/e2e/golden_path.py --base http://127.0.0.1:4000 --video tests/.fixtures/sample.mp4          # acceptance
```

Browser end-to-end coverage lives in the frontend repo (Playwright).

## Key behaviours

- **Direct uploads**: signed single or multipart PUT straight to storage (parallel parts, retries, on-demand re-signing). `upload-complete` is idempotent and enqueues ingestion exactly once.
- **Durable, resumable pipeline**: every stage is a BullMQ job with a deterministic job ID; PostgreSQL holds authoritative state; workers are idempotent and crash-safe; async transcription resumes from the persisted provider job ID.
- **Highlight discovery**: 5-min windows with 1-min overlap → structured LLM proposals → temporal-IoU dedupe → sentence-boundary refinement → second-pass list-relative ranking → weighted 0–100 score. Zero candidates is a valid result.
- **Rendering on demand**: face detection only inside the chosen window, tracked subject, smoothed/velocity-limited virtual camera, center/fit fallback, word-timed ASS captions with active-word emphasis, one final encode.
- **Rerenders** never re-transcribe or re-analyze; each is a new immutable version.

See [docs/pipeline.md](docs/pipeline.md) for the full technical design.
