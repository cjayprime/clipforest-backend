# ClipRover — backend

The server half of an AI short-form video clipping platform: ingest a long video, transcribe it, find the self-contained moments worth clipping, and render any of them as a captioned, subject-aware **H.264/AAC MP4 in 9:16, 4:5, 1:1 or 16:9**. Built to the PRD in [docs/AI_Short_Form_Video_Clipping_Product_Requirements.pdf](docs/AI_Short_Form_Video_Clipping_Product_Requirements.pdf).

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
| `docs/` | `pipeline.md`, `auth.md`, `billing.md`, `deployment.md`, `runbook.md`, the PRD. |
| `tests/` | Every suite — see [tests/README.md](tests/README.md). |
| `package.json`, `eslint.config.mjs` | Repo-level tooling only: one lint pass over `api/src` and `tests/api`. Run `npm install` here as well as in `api/`. |

## Quick start

Docker runs only what needs it — PostgreSQL, Redis, S3-compatible storage and the Python media worker (FFmpeg, OpenCV). The API runs natively.

```bash
cp .env.example .env                                            # works as-is; add API keys for real providers
cd infra && docker compose --env-file ../.env up -d --build      # postgres, redis, minio, worker
cd ../api && npm install && npm run start:dev                    # API on :4000
```

Then start the web app from the frontend repo (`npm run dev`, :3000) and create an account — **no user is seeded**, and registration is immediate with no verification step.

Email (welcome, password reset, password changed) goes through Brevo when `BREVO_API_KEY` is set. Without a key it is printed to the API log instead, so the password reset flow works offline — the reset link appears in your terminal. See [docs/auth.md](docs/auth.md).

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
PYTHONPATH=src DATABASE_URL=... REDIS_URL=... S3_ENDPOINT=http://localhost:9000 python -m cliprover_worker.main
```

## Database changes

Entities live in `api/src/entities` (`user.entity.ts`, `video.entity.ts`, …) and are exported, together with the `ENTITIES` list, from `entities/index.ts`. Relations use lazy `() => Entity` callbacks, which keeps the circular imports between `Video`, `User`, `Transcript`, `Candidate` and `Render` safe.

Two conventions worth knowing before you read a query:

- **Primary keys are bigint identities named after their table** — `users.user_id`, `videos.video_id`, `candidates.candidate_id` — and the TypeScript property matches the column exactly, so a join reads the same in both. Identifier properties are snake_case for that reason; everything else stays camelCase with an explicit `name` (`passwordHash` → `password_hash`).
- **IDs are `string` in TypeScript and JSON.** TypeORM represents `bigint` as a string, which is the safe way to carry a 64-bit value through JavaScript. The serializers in `common/serializers.ts` are where entity properties (`video_id`) become API fields (`id`).

The schema is owned by checked-in migrations in `api/src/migrations`. The Python worker reads the same tables with raw SQL, so **column names are a cross-runtime contract**.

Two registrations are easy to forget, and both fail silently rather than loudly:

- A new **entity** must be added to `ENTITIES` in `entities/index.ts`, or the DataSource never sees it.
- A new **migration** must be imported and appended to the `migrations` array in `core/data-source.ts` — it is an explicit list, not a glob, so an unregistered migration simply never runs and the API still boots clean.

After changing an entity: `npm run build && npm run migration:generate`, review the SQL, register the migration, then restart the API (or `npm run migration:run`).

## Linting

ESLint lives at the **repo root**, not inside `api/`, because a flat config can only lint files under its own directory and the suites in `tests/` are outside `api/`. One pass covers both:

```bash
npm install            # in backend/ — the lint toolchain only; the API has its own package.json
npm run lint           # api/src + tests/api
npm run lint:fix
```

The ruleset is typescript-eslint `strictTypeChecked` + `stylisticTypeChecked` with type information (`projectService`). Two rules are configured rather than followed blindly, and both have the reason in [eslint.config.mjs](eslint.config.mjs): `prefer-nullish-coalescing` ignores strings (`||` on `process.env.X` is deliberate — `??` would treat an empty key as configured), and `restrict-template-expressions` allows numbers.

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

- **Accounts**: sign up, sign in, forgot/reset password and change password, with single-use hashed reset links and sessions that end on other devices when the password changes. See [docs/auth.md](docs/auth.md).
- **Billing**: a Polar subscription is a purchase of credits, split at grant time into a bucket that rolls over and one that expires at renewal (`CREDIT_ROLLOVER_SHARE`, default 90%). Credits are granted only by verified, deduplicated webhooks — never on the checkout redirect. Without `POLAR_ACCESS_TOKEN` billing is simply off and the UI hides it. See [docs/billing.md](docs/billing.md).

See [docs/pipeline.md](docs/pipeline.md) for the full technical design.
