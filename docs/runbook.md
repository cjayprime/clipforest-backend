# Runbook

All commands run from this repo's `infra/` with `--env-file ../.env`. Every user-facing error carries a reference (correlation) ID; search logs with `docker compose logs api worker | grep <id>`.

## Inspect state

```bash
docker compose ps
docker compose logs -f --tail=200 worker
docker compose exec postgres psql -U cliprover -c "select id,status,stage,substage,progress,error_code,updated_at from videos order by updated_at desc limit 20;"
docker compose exec postgres psql -U cliprover -c "select queue_name,external_job_id,attempt,status,error_code,duration_ms from job_runs order by started_at desc limit 30;"
docker compose exec redis redis-cli --scan --pattern 'cliprover:*:wait' | xargs -I{} sh -c 'echo {} $(docker compose exec -T redis redis-cli llen {})'
curl -s localhost:4000/api/metrics | grep cliprover_queue_jobs
```

## Transcription provider outage

Symptoms: `TRANSCRIPTION_PROVIDER_ERROR` / `TRANSCRIPTION_RATE_LIMITED`, videos sitting in `TRANSCRIBING` with substage "retrying". Jobs retry 5× with exponential backoff; AssemblyAI jobs resume from their saved provider job ID. If the outage is long: switch provider (`TRANSCRIPTION_PROVIDER=deepgram` + key), `docker compose up -d worker`, then retry failed videos from the UI (they resume at transcription because the audio is still stored). Credential errors surface as `SYSTEM_MISCONFIGURED` (non-retryable) — fix the key, then retry.

## LLM outage / invalid output

`ANALYSIS_LLM_ERROR` (retryable) or `ANALYSIS_INVALID_OUTPUT`. The transcript is saved, so a retry only re-runs analysis. As a stopgap set `LLM_PROVIDER=heuristic` (lower-quality offline scorer).

## FFmpeg failure

`RENDER_FFMPEG_FAILED` with the FFmpeg stderr tail in the worker log and `job_runs.error_message`. One automatic retry after a clean workspace. Reproduce: `docker compose exec worker sh`, rebuild the command from the log. Common causes: undecodable source (use `framingMode=fit`/retry after proxy), OOM (`docker stats`; lower `FFMPEG_THREADS`, keep render concurrency 1), missing fonts (`fc-list | grep -i inter`).

## Redis unavailable

API requests that enqueue work fail with 5xx and the UI shows a retryable error; the SSE stream drops and clients fall back to polling. Restart: `docker compose restart redis`. With AOF, queued jobs survive. Afterwards the janitor (every 10 min) re-enqueues videos/renders stuck in `QUEUED`; videos stuck mid-stage (`INGESTING`/`TRANSCRIBING`/`ANALYZING` with no active job) are reclaimed by BullMQ's stalled-job check once workers reconnect.

## R2 unavailable

Uploads fail in the browser (retried per part), `STORAGE_*` errors in workers (retryable, bounded attempts). Check `/api/health/ready` → `storage:false`. When R2 recovers, retry failed videos/renders from the UI. Never change `S3_BUCKET` on a running system — object keys are deterministic per bucket.

## Low disk

Heavy jobs fail fast with `SYSTEM_LOW_DISK` (retryable) before breaching `DISK_MIN_FREE_BYTES`. Check `df -h`, `docker system df`. Free space: `docker compose exec worker sh -c 'du -sh /tmp/video-jobs/*'` (stale dirs are swept after 6 h), `docker image prune -f`, `docker builder prune -f`, rotate logs. Then retry.

## Stuck queue

Symptoms: jobs waiting, none active. Check the worker is running and consuming (`WORKER_QUEUES`), look for a job stuck `active` beyond its lock (it will be moved back to wait by the stalled checker within ~1 min of the worker dying). Restart the worker: `docker compose restart worker` — jobs are idempotent. To inspect a job: `docker compose exec redis redis-cli hgetall cliprover:render:<jobId>`.

## Deleting user content

`DELETE /api/videos/:id` soft-deletes immediately and enqueues `cleanup.purge-video.<id>`, which deletes every object under `users/{userId}/videos/{videoId}/`, aborts open multipart uploads and hard-deletes the rows. If Redis was down, the janitor re-schedules unpurged deletions.

## Restore from backup

Stop api/worker, `pg_restore` (see deployment.md), start api (migrations apply), start worker. Videos that were mid-processing at backup time can be retried from the UI.
