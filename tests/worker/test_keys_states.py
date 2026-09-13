from cliprover_worker import keys
from cliprover_worker.states import can_transition_render, can_transition_video, render_sources_for, video_sources_for
from cliprover_worker.workspace import Workspace, ensure_disk
from cliprover_worker import errors

import pytest

U, V, R = "u-1", "v-2", "r-3"


def test_object_layout_matches_prd():
    assert keys.source_key(U, V, "a.mp4") == "users/u-1/videos/v-2/source/a.mp4"
    assert keys.audio_key(U, V) == "users/u-1/videos/v-2/derived/audio.mp3"
    assert keys.render_output_key(U, V, R) == "users/u-1/videos/v-2/renders/r-3/final.mp4"
    assert keys.render_thumb_key(U, V, R) == "users/u-1/videos/v-2/renders/r-3/thumb.jpg"
    assert keys.is_owned_key(keys.render_output_key(U, V, R), U, V)
    assert not keys.is_owned_key("users/u-1/videos/other/x", U, V)
    assert not keys.is_owned_key("users/u-1/videos/v-2/../x", U, V)


def test_job_ids_match_api_conventions():
    assert keys.JobIds.ingest(V, "2026-09-mvp1", 1) == "ingest.v-2.2026-09-mvp1.r1"
    assert keys.JobIds.transcription(V, 2, 3) == "transcribe.v-2.v2.r3"
    assert keys.JobIds.analysis(V, "mvp1", 4) == "analyze.v-2.mvp1.a4"
    assert keys.JobIds.render(R, 2) == "render.r-3.a2"
    assert ":" not in keys.JobIds.cleanup("purge-video", V)


def test_state_machines_match_prd():
    assert can_transition_video("QUEUED", "INGESTING")
    assert can_transition_video("READY", "ANALYZING")
    assert not can_transition_video("READY", "QUEUED")
    assert not can_transition_render("COMPLETED", "QUEUED")
    assert set(video_sources_for("FAILED")) == {"CREATED", "UPLOADING", "QUEUED", "INGESTING", "TRANSCRIBING", "ANALYZING"}
    assert render_sources_for("COMPLETED") == ("UPLOADING",)


def test_workspace_is_isolated_and_always_removed(tmp_path):
    ws = Workspace(str(tmp_path), "render.abc/../evil:1")
    with ws:
        assert ws.path.parent == tmp_path
        ws.file("x.txt").write_text("hi")
    assert not ws.path.exists()
    try:
        with Workspace(str(tmp_path), "boom") as ws2:
            ws2.file("y").write_text("y")
            raise RuntimeError("fail")
    except RuntimeError:
        pass
    assert not (tmp_path / "boom").exists()


def test_disk_guard_raises_retryable_error(tmp_path):
    with pytest.raises(errors.PipelineError) as info:
        ensure_disk(str(tmp_path), reserve_bytes=10**18)
    assert info.value.code == "SYSTEM_LOW_DISK" and info.value.retryable
