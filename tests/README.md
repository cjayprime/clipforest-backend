# Tests

Every backend suite lives here and runs against the code in `api/` and `worker/`. Browser tests belong to the frontend repo.

| Folder | Suite | Run it |
| --- | --- | --- |
| `api/` | API unit tests (state machines, idempotency keys, object keys, SSRF validation, render ranges) | `cd api && npm test` |
| `worker/` | Worker unit tests + FFmpeg/OpenCV integration tests over generated fixtures | host: `cd worker && .venv/Scripts/python -m pytest ../tests/worker -m "not ffmpeg"` · container (all): `cd infra && docker compose --env-file ../.env run --rm --no-deps worker python -m pytest -q /app/tests` |
| `e2e/` | API-level acceptance run against a live stack (PRD §22) | `python tests/e2e/golden_path.py --base http://127.0.0.1:4000 --video tests/.fixtures/sample.mp4` |

The FFmpeg-marked worker tests need `ffmpeg`/`ffprobe` on PATH, which is why they run in the worker container; `-m "not ffmpeg"` skips them on the host.

On Windows with Git Bash, prefix the container command with `MSYS_NO_PATHCONV=1` or `/app/tests` is rewritten into a Windows path.

## Prerequisites for the acceptance run

```bash
cd infra && docker compose --env-file ../.env up -d     # postgres, redis, minio, worker
cd api && npm run start:dev                             # API on :4000
```

With no provider keys set it runs fully offline (mock transcription + heuristic analysis) and still produces a real 1080×1920 MP4.

## Fixtures

`tests/.fixtures/sample.mp4` (gitignored) is generated, not committed:

```bash
mkdir -p tests/.fixtures
docker run --rm -v "$PWD/tests/.fixtures:/out" clipforest-worker \
  ffmpeg -v error -y -f lavfi -i testsrc2=size=640x360:rate=24 -f lavfi -i sine=frequency=300 \
  -t 100 -c:v libx264 -preset veryfast -crf 38 -pix_fmt yuv420p -c:a aac -b:a 64k /out/sample.mp4
```

It is a synthetic test pattern with no faces, so renders correctly fall back to center crop — face tracking is covered by `worker/test_reframe.py` instead.

The worker's FFmpeg tests generate their own fixtures (landscape, portrait, no-audio, corrupt) into a temp directory on first run.
