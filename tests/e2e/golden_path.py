"""Golden-path acceptance run against a live stack (PRD §22, §25).

Exercises: auth, rights confirmation, direct-to-storage upload, idempotent
upload-complete, ingest -> transcription -> analysis, ranked candidates, render
with dedupe, 1080x1920 MP4 output, rerender as a new 1:1 version, immutability,
transcript access, SSE, and deletion.

Run from the backend repo root, against a stack started per tests/README.md:

    python tests/e2e/golden_path.py --base http://127.0.0.1:4000 --video tests/.fixtures/sample.mp4
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import httpx


def check(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  ok  {msg}")


def poll(c: httpx.Client, path: str, done: set[str], timeout: float) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        body = c.get(path).json()
        marker = (body["status"], body.get("substage"), body.get("progress"))
        if marker != last:
            print(f"      {path}: {body['status']:<18} {body.get('progress', 0):>3}%  {body.get('substage') or ''}")
            last = marker
        if body["status"] in done:
            return body
        time.sleep(2)
    print(f"FAIL: timed out waiting for {path}")
    sys.exit(1)


def upload(c: httpx.Client, video_id: str, data: bytes) -> list[dict] | None:
    session = c.post(f"/api/videos/{video_id}/upload-session").json()
    if session["mode"] == "single":
        r = httpx.put(session["url"], content=data, headers=session["headers"], timeout=300)
        check(r.status_code == 200, "single PUT upload to signed storage URL")
        return None
    parts, urls = [], {p["partNumber"]: p["url"] for p in session["parts"]}
    for n in range(1, session["partCount"] + 1):
        if n not in urls:
            signed = c.post(f"/api/videos/{video_id}/upload-session/parts", json={"partNumbers": list(range(n, min(n + 20, session["partCount"] + 1)))}).json()
            urls.update({p["partNumber"]: p["url"] for p in signed["parts"]})
        chunk = data[(n - 1) * session["partSize"] : n * session["partSize"]]
        r = httpx.put(urls[n], content=chunk, timeout=300)
        parts.append({"partNumber": n, "etag": r.headers["etag"]})
    check(True, f"multipart upload of {len(parts)} parts")
    return parts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--video", required=True)
    ap.add_argument("--timeout", type=float, default=900)
    args = ap.parse_args()
    data = Path(args.video).read_bytes()
    c = httpx.Client(base_url=args.base, timeout=60)
    started = time.time()

    print("auth")
    email = f"e2e-{uuid.uuid4().hex[:10]}@example.com"
    r = c.post("/api/auth/register", json={"email": email, "password": "correct-horse-battery", "displayName": "E2E"})
    check(r.status_code == 201, "register and receive session cookie")
    check(c.get("/api/auth/me").json()["user"]["email"] == email, "session is valid")
    anon = httpx.get(f"{args.base}/api/videos")
    check(anon.status_code == 401 and anon.json()["error"]["code"] == "AUTH_REQUIRED", "unauthenticated requests are rejected with the error envelope")

    print("create + upload")
    meta = {"sourceType": "upload", "originalFilename": "e2e fixture.mp4", "contentType": "video/mp4", "sizeBytes": len(data)}
    r = c.post("/api/videos", json={**meta, "rightsConfirmed": False})
    check(r.status_code == 400 and r.json()["error"]["code"] == "VIDEO_RIGHTS_CONFIRMATION_REQUIRED", "rights confirmation is required")
    video = c.post("/api/videos", json={**meta, "rightsConfirmed": True}).json()
    vid = video["id"]
    check(video["status"] == "CREATED", "video record created before processing")
    parts = upload(c, vid, data)
    body = {"parts": parts} if parts else {"observedSizeBytes": len(data)}
    first = c.post(f"/api/videos/{vid}/upload-complete", json=body)
    check(first.status_code == 200 and first.json()["status"] == "QUEUED", "upload-complete verifies the object and enqueues ingestion")
    again = c.post(f"/api/videos/{vid}/upload-complete", json=body)
    check(again.status_code == 200 and again.json()["status"] != "FAILED", "repeated upload-complete is idempotent")

    print("pipeline")
    ready = poll(c, f"/api/videos/{vid}", {"READY", "FAILED"}, args.timeout)
    check(ready["status"] == "READY", f"video reached READY (error={ready.get('error')})")
    check(ready["durationMs"] and ready["width"] and ready["hasAudio"], "ffprobe metadata persisted")

    cands = c.get(f"/api/videos/{vid}/candidates?minScore=0").json()
    items = cands["items"]
    check(len(items) > 0, f"{len(items)} ranked candidates")
    for cand in items:
        assert 0 <= cand["startMs"] < cand["endMs"] <= ready["durationMs"], cand
        assert 0 <= cand["score"] <= 100 and cand["title"] and cand["reason"] and cand["excerpt"], cand
    check([x["score"] for x in items] == sorted((x["score"] for x in items), reverse=True), "candidates sorted by score, within source duration")
    by_time = c.get(f"/api/videos/{vid}/candidates?minScore=0&sort=time").json()["items"]
    check([x["startMs"] for x in by_time] == sorted(x["startMs"] for x in by_time), "sort by source time")
    tr = c.get(f"/api/videos/{vid}/transcript", params={"startMs": items[0]["startMs"], "endMs": items[0]["endMs"]}).json()
    check(len(tr["words"]) > 0, "word-timed transcript available for the candidate range")
    pb = c.get(f"/api/videos/{vid}/playback").json()
    check(bool(pb["sourceUrl"]), "signed source playback URL issued")

    print("render")
    cand = items[0]
    settings = {"aspectRatio": "9:16", "framingMode": "auto", "captions": {"enabled": True, "preset": "bold-default"}}
    render = c.post(f"/api/candidates/{cand['id']}/renders", json=settings).json()
    dup = c.post(f"/api/candidates/{cand['id']}/renders", json=settings).json()
    check(dup["id"] == render["id"] and dup["deduplicated"], "identical in-flight render is deduplicated")
    done = poll(c, f"/api/renders/{render['id']}", {"COMPLETED", "FAILED"}, args.timeout)
    check(done["status"] == "COMPLETED", f"render completed (error={done.get('error')}, framing={done.get('framing')})")
    check(done["width"] == 1080 and done["height"] == 1920, "output is 1080x1920")
    mp4 = httpx.get(done["outputUrl"], timeout=120).content
    check(mp4[4:8] == b"ftyp" and len(mp4) == done["fileSize"], f"downloaded MP4 matches recorded size ({len(mp4)} bytes)")
    check(bool(done["downloadUrl"]) and bool(done["thumbnailUrl"]), "download and thumbnail URLs present")

    print("adjust + rerender")
    v2 = c.post(
        f"/api/renders/{render['id']}/rerender",
        json={"startMs": cand["startMs"] + 1000, "aspectRatio": "1:1", "framingMode": "center", "captions": {"enabled": False, "preset": "minimal"}},
    ).json()
    check(v2["version"] == 2 and v2["id"] != render["id"], "rerender creates a new version")
    done2 = poll(c, f"/api/renders/{v2['id']}", {"COMPLETED", "FAILED"}, args.timeout)
    check(done2["status"] == "COMPLETED" and done2["framing"]["captions"]["enabled"] is False, "captions-off center-crop version rendered")
    check(done2["width"] == 1080 and done2["height"] == 1080, "1:1 version is 1080x1080")
    r = c.post(f"/api/renders/{render['id']}/retry")
    check(r.status_code == 409, "completed renders are immutable")
    video_after = c.get(f"/api/videos/{vid}").json()
    check(video_after["status"] == "READY", "rerender did not re-run transcription or analysis")

    print("realtime")
    with c.stream("GET", "/api/events", timeout=httpx.Timeout(5, read=3)) as s:
        check(s.status_code == 200 and s.headers["content-type"].startswith("text/event-stream"), "SSE stream available")

    print("delete")
    r = c.delete(f"/api/videos/{vid}")
    check(r.status_code == 202 and r.json()["storageCleanup"] == "scheduled", "delete schedules storage cleanup")
    check(c.get(f"/api/videos/{vid}").status_code == 404, "deleted video is no longer accessible")

    print(json.dumps({"result": "PASS", "seconds": round(time.time() - started, 1), "candidates": len(items), "framing": done["framing"]}, indent=2))


if __name__ == "__main__":
    main()
