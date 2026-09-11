# Pipeline design

## Stages and queues

| Queue | Producer | Work | Concurrency (8 GB host) | Attempts / backoff |
| --- | --- | --- | --- | --- |
| `video-ingest` | API (upload-complete, URL import, retry) · worker janitor | verify source / yt-dlp import, ffprobe validation, poster frame, optional browser proxy, speech audio extraction | 1 | 3 · exp 10 s |
| `transcription` | worker (after ingest) · API (retry) | provider call, normalization, persistence | 3 (1 for local Whisper) | 5 · exp 15 s |
| `analysis` | worker (after transcription) · API (re-analyze, retry) | windows → proposals → dedupe → refine → rank | 6 | 3 · exp 10 s |
| `render` | API (Generate / rerender / retry) · worker janitor | clip-local face analysis, crop path, captions, encode, upload | **1** | 2 · exp 30 s |
| `cleanup` | API (delete) · worker janitor tick | purge deleted videos, retention, self-healing re-enqueues | 1 | 5 · exp 30 s |

BullMQ prefix `clipforest`. The Node API uses `bullmq@6.3.4`; the worker uses the Python `bullmq==3.2.1`, which ships the identical Lua script set (verified), so both sides interoperate on the same queues.

Payloads (IDs and immutable parameters only) are defined in `contracts/queues.schema.json`.

### Job IDs = idempotency keys

| Queue | Job ID |
| --- | --- |
| ingest | `ingest.{videoId}.{pipelineVersion}.r{processingRun}` |
| transcription | `transcribe.{videoId}.v{transcriptVersion}.r{processingRun}` |
| analysis | `analyze.{videoId}.{analysisVersion}.a{analysisRun}` |
| render | `render.{renderId}.a{attempt}` |
| cleanup | `cleanup.{kind}.{entityId}` |

BullMQ ignores an `add()` whose ID already exists, so refreshes, retried callbacks and janitor re-enqueues never duplicate work. Run counters (`processing_run`, `analysis_run`, render `attempt`) are incremented only by conditional (compare-and-set) status transitions, so exactly one caller wins.

### Crash safety

- Workers renew locks; stalled jobs are reclaimed (`stalledInterval` 30 s, `maxStalledCount` 2).
- Every handler re-reads durable state first and skips work already done (e.g. a `COMPLETED` transcript row is never re-transcribed; a video already in `TRANSCRIBING` just ensures the transcription job exists).
- Side effects are upserts or unique keys: one transcript row per `(video, version)`, candidates replaced per `analysis_run` in one transaction, render outputs at deterministic keys, usage events with unique idempotency keys.
- Non-retryable failures (corrupt media, no audio, private source, bad credentials) raise `UnrecoverableError` and fail immediately; transient ones retry with backoff; the final failure writes a stable `error_code`, message, retryability and correlation ID on the entity.
- The janitor re-enqueues rows stuck in `QUEUED` (lost enqueue), re-schedules unpurged deletions, removes stale local working directories, and applies retention.

## State machines

Video: `CREATED → UPLOADING → QUEUED → INGESTING → TRANSCRIBING → ANALYZING → READY`; any processing state → `FAILED`; `FAILED → QUEUED | TRANSCRIBING | ANALYZING` only through `POST /videos/:id/process` (resumes from the furthest durable stage: saved transcript → analysis; saved audio → transcription; otherwise ingest); `READY → ANALYZING` only through re-analyze.

Render: `QUEUED → PREPARING → ANALYZING_VISUALS → RENDERING → UPLOADING → COMPLETED`; active → `FAILED`; `FAILED → QUEUED` via retry; `COMPLETED` is immutable — settings changes create a new version in the same lineage (`lineage_key`, `version`, `is_latest`).

Both tables are enforced in TypeScript (`api/src/common/state-machine.ts`), Python (`worker/src/clipforest_worker/states.py`) and by SQL CHECK constraints in the initial migration.

## Progress

Video progress: ingest 0–15 %, transcription 15–50 %, analysis 50–85 %, ranking/finalization 85–100 %. Render progress is separate (0–100 %). Workers write `progress`, `stage` and `substage` to PostgreSQL (throttled) and publish to Redis channel `clipforest:events`; the API fans events out per user over SSE (`GET /api/events`). Clients refetch GET endpoints on (re)connect and fall back to polling, so no state depends on a missed event. No ETA is shown.

## Ingestion

- Direct upload: `POST /videos` (rights confirmation required) → `upload-session` (single PUT < 64 MiB, otherwise multipart with equal 16 MiB+ parts, ≤ 10 000 parts, URLs signed in batches) → browser uploads to R2 → `upload-complete` (completes multipart, `HeadObject` verification, size limit) → `QUEUED`.
- URL import: only allow-listed YouTube hosts; the URL is rebuilt from the parsed video ID (API and worker both validate); yt-dlp runs with `allowed_extractors=['youtube']`, no playlists, size/duration limits; private/age-gated/removed sources fail non-retryably.
- Validation: ffprobe (container, codecs, duration, dimensions incl. rotation, fps, audio), a real decoded frame (poster) to catch corrupt media, configurable max size/duration, audio required.
- A 720p H.264 proxy is generated only when the source can't play in browsers (e.g. HEVC, ProRes, MKV).
- By default FFmpeg reads the source through a signed URL with HTTP range requests (`SOURCE_READ_MODE=url`), so multi-GB sources are never fully downloaded to the worker.

## Transcription

`TranscriptionProvider.transcribe(audio, ctx) -> RawTranscript`, normalized into the PRD schema (`language, durationMs, fullText, segments[{id,startMs,endMs,text,speaker}], words[{startMs,endMs,text,confidence,speaker}], provider, providerMetadata`). Adapters: AssemblyAI (async, resumable), Deepgram, OpenAI-compatible Whisper API (auto-chunks > 24 MB), local faster-whisper (opt-in, serialized with renders), mock. Normalization sorts/clamps/de-overlaps word timings (monotonic ratio recorded) and builds ≤ 20 s sentence-like segments with stable IDs. Audio is mono 16 kHz MP3 (no WAV); the derived audio object is deleted after the transcript is saved.

## Highlight discovery

1. **Windows**: 5 min, 1 min overlap (configurable). Each transcript line is sent as `[s12 | 05:12.3-05:18.9] (speaker) text`.
2. **Proposals**: the LLM returns 0–5 candidates per window as structured output (`start_segment`, `end_segment`, title, hook, summary, reason, category, six 0–10 scores). Providers are interchangeable and chosen by whichever key is set — Claude via `client.beta.messages.parse(output_format=…)` (with server-side refusal fallback) or GPT via `client.chat.completions.parse(response_format=…)`, falling back to the offline heuristic scorer. Both map failures onto the same retry policy: invalid or truncated output is re-prompted (repair), a refusal yields no candidates for that window, auth/model errors are non-retryable, and output that stays invalid fails the stage retryably. Segment IDs outside the window are discarded, so timestamps can't be hallucinated.
3. **Dedupe**: temporal IoU > 0.5 keeps the stronger candidate; if the weaker one is more complete, the kept range expands to the union (bounded by max length).
4. **Refine**: snap to segment starts/ends; pull in prior lines when starting on a dangling conjunction or unresolved pronoun; extend to a sentence end; enforce 20–90 s; strip leading fillers; 250 ms pre-roll / 350 ms post-roll bounded by neighbouring words; dedupe again.
5. **Rank**: a second LLM pass re-scores the shortlist list-relatively (failure is non-fatal). Score = 25 % hook + 20 % clarity + 15 % novelty + 15 % emotion + 15 % completeness + 10 % shareability, ×10 → 0–100. Up to 15 are kept; component scores are persisted.

Re-analysis supersedes older candidates (soft) and never touches rendered clips. The UI hides candidates below `MIN_CANDIDATE_SCORE` (40) with a one-click "show lower-scored" and a manual range form — scores are never inflated.

## Rendering

1. Disk guard, range validation, source via signed URL.
2. **Faces** (framing `auto`): frames of the selected window only, at 3 fps and 640 px wide, piped from FFmpeg; OpenCV YuNet (Haar fallback).
3. **Tracking & subject**: IoU/centroid tracks; the primary subject is the most consistently visible, central, large face; switches require a 2.5 s dwell with the current subject absent.
4. **Camera path**: face centre with 38 % headroom → dead-zone → zero-phase EMA → velocity limit (60 % of crop width per second) → safe-framing nudge → clamp to source bounds; interpolated per output frame and applied via an FFmpeg `sendcmd` script driving `crop`. Coverage < 30 % falls back to `FALLBACK_FRAMING` (center or blurred-background fit). The chosen strategy and stats are stored in `renders.framing`.
5. **Captions**: clip-local words from the persisted transcript (fillers and stutters removed, wording unchanged), grouped by gaps/punctuation/length into balanced 1–2 line phrases that never appear before the first word; ASS with one event per word so the active word is highlighted monotonically; four presets; lines scaled to stay within 90 px safe margins.
6. **Encode**: one pass — `setpts`, CFR fps (source fps when 23–60), crop/scale 1080×1920 (or blur-fit), `ass`, libx264 High / yuv420p / CRF 20, AAC 160 kbps 48 kHz, `+faststart`, metadata stripped.
7. **Verify** with ffprobe (dimensions, duration ±600 ms) → thumbnail → upload `renders/{id}/final.mp4` + `thumb.jpg` → `COMPLETED` + usage event.

## Storage layout

```
users/{userId}/videos/{videoId}/source/{sanitizedName}
users/{userId}/videos/{videoId}/derived/audio.mp3      (temporary)
users/{userId}/videos/{videoId}/derived/thumb.jpg
users/{userId}/videos/{videoId}/derived/proxy.mp4      (only if needed)
users/{userId}/videos/{videoId}/renders/{renderId}/final.mp4
users/{userId}/videos/{videoId}/renders/{renderId}/thumb.jpg
```

Keys are always derived server-side from owned IDs. Local work happens in `/tmp/video-jobs/{queue}-{jobId}-a{attempt}/`, removed in a `finally` path; the janitor sweeps leftovers older than 6 h.
