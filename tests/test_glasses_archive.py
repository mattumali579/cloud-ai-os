"""Offline archive tests. No Drive folder id, no token, no OpenAI, no glasses frame."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

import pytest

from cloudos.glasses.archive.crypto import decrypt_bytes, encrypt_bytes
from cloudos.glasses.archive.index import Index
from cloudos.glasses.archive.rclone import copy_args, cryptcheck_args, probe
from cloudos.glasses.archive.schema import SchemaError, build_event
from cloudos.glasses.archive.session import MediaDisabled, SessionBundle, SpoolFull
from cloudos.glasses.archive.upload import (
    ChecksumMismatch,
    ScriptedTransport,
    drain,
    refuse_remote_delete,
    upload_session,
)

PASSPHRASE = "test-only-passphrase"
WHEN = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)


def _event(session_id: str, **kwargs) -> dict:
    base = dict(
        session_id=session_id,
        source="prerecorded",
        model_name="scripted-yolo",
        model_version="none",
        t=WHEN,
        detections=[{"class": "truck", "confidence": 0.9, "bbox_norm": [0.1, 0.2, 0.3, 0.4]}],
        track_id="trk-0001",
        selection_method="voice",
        frame_count=1,
    )
    base.update(kwargs)
    return build_event(**base)


def _bundle(tmp_path, **kwargs) -> SessionBundle:
    bundle = SessionBundle(tmp_path / "spool", passphrase=PASSPHRASE, now=WHEN, **kwargs)
    bundle.add_event(_event(bundle.session_id))
    return bundle


def test_event_round_trip_rejects_invented_depth_and_bad_landmarks():
    record = _event("11111111-1111-1111-1111-111111111111")
    again = json.loads(json.dumps(record))
    assert again["schema"] == "glasses.archive.event.v1"
    assert again["t"].endswith("Z")
    assert again["selection"]["durable_identity"] is False
    assert again["camera"]["persistent_phone_store"] is False
    assert again["motion"] is None
    with pytest.raises(SchemaError):
        build_event(
            session_id="s",
            source="x",
            model_name="m",
            model_version="0",
            t=WHEN,
            motion={"dx_px": 3, "unit": "pixels", "calibrated": False, "confidence": 0.2, "depth_m": 4},
        )
    with pytest.raises(SchemaError):
        build_event(
            session_id="s",
            source="x",
            model_name="m",
            model_version="0",
            t=WHEN,
            gesture="pinch",
            landmarks=[[0, 0]] * 20,
        )
    with pytest.raises(SchemaError):
        build_event(session_id="s", source="x", model_name="m", model_version="0", t=WHEN, hardware_verified=True)


def test_encrypt_round_trip_and_chunk_hashes(tmp_path):
    blob = encrypt_bytes(b"events", PASSPHRASE)
    assert decrypt_bytes(blob, PASSPHRASE) == b"events"
    with pytest.raises(Exception):
        decrypt_bytes(blob, "other-passphrase")
    ready = _bundle(tmp_path, chunk_bytes=40).close()
    state = json.loads((ready / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "ready"
    assert state["remote_prefix"].startswith("sessions/2026/11/16/")
    names = [item["name"] for item in state["files"]]
    assert any(name.startswith("events-") and name.endswith(".jsonl.gz.enc") for name in names)
    assert not list(ready.rglob("*.jsonl"))
    cipher = next(ready.glob("events-*.jsonl.gz.enc")).read_bytes()
    plain = gzip.decompress(decrypt_bytes(cipher, PASSPHRASE))
    line = json.loads(plain.splitlines()[0])
    assert line["detections"][0]["class"] == "truck"
    manifest = json.loads(decrypt_bytes((ready / "manifest.json.enc").read_bytes(), PASSPHRASE))
    assert manifest["face_biometrics"] is False
    assert manifest["files"][0]["sha256"]


def test_decrypt_tree_matches_the_manifest(tmp_path, monkeypatch):
    ready = _bundle(tmp_path).close()
    monkeypatch.setenv("GLASSES_ARCHIVE_PASSPHRASE", PASSPHRASE)
    from cloudos.glasses.archive.__main__ import main

    assert main([str(ready), str(tmp_path / "plain")]) == 0
    manifest = json.loads((tmp_path / "plain" / "manifest.json").read_text(encoding="utf-8"))
    import hashlib

    for item in manifest["files"]:
        body = (tmp_path / "plain" / item["name"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == item["sha256"]


def test_media_off_by_default_and_spool_cap(tmp_path):
    bundle = _bundle(tmp_path)
    with pytest.raises(MediaDisabled):
        bundle.add_media("snap.ppm", b"not-a-photo")
    with pytest.raises(MediaDisabled):
        SessionBundle(tmp_path / "other", passphrase=PASSPHRASE, allow_media=True).add_media(
            "face-template.json", b"{}"
        )
    tiny = SessionBundle(tmp_path / "cap", passphrase=PASSPHRASE, max_spool_bytes=1, now=WHEN)
    with pytest.raises(SpoolFull):
        tiny.add_event(_event(tiny.session_id))


def test_retry_idempotent_copy_and_checksum_keeps_the_spool(tmp_path):
    ready = _bundle(tmp_path).close()
    delays: list[float] = []
    transport = ScriptedTransport(fail_times=2)
    result = upload_session(ready, transport, sleeper=delays.append)
    assert result["status"] == "committed"
    assert delays == [1.0, 2.0]
    assert transport.copies
    again = upload_session(ready, transport, sleeper=delays.append)
    assert again["uploaded"] == 0
    assert ready.exists()

    corrupt_root = _bundle(tmp_path / "bad").close()
    bad = ScriptedTransport(corrupt=True)
    with pytest.raises(ChecksumMismatch):
        upload_session(corrupt_root, bad)
    assert corrupt_root.exists()
    assert (corrupt_root / "state.json").read_text(encoding="utf-8").find('"ready"') > 0


def test_drain_deletes_only_after_success_and_keeps_a_searchable_index(tmp_path):
    spool = tmp_path / "spool"
    ready = _bundle(tmp_path).close()
    assert ready.parent.parent.parent == spool / "ready" or ready.is_relative_to(spool)
    outcome = drain(spool, ScriptedTransport())
    assert outcome["status"] == "ok"
    assert outcome["committed"]
    assert outcome["remote_delete"] is False
    assert not ready.exists()
    found = Index(spool / "index.jsonl").search(label="truck", track_id="trk-0001", day="2026-11-16")
    assert len(found) == 1
    assert "landmarks" not in json.dumps(found[0])
    states = [
        {
            "session_id": "rebuilt",
            "created_at": "2026-11-16T00:00:00Z",
            "remote_prefix": "sessions/2026/11/16/rebuilt",
            "labels": ["cup"],
            "track_ids": ["trk-0002"],
            "media_opt_in": False,
        }
    ]
    index = Index(spool / "index.jsonl")
    index.rebuild(states)
    assert index.search(label="cup")[0]["session_id"] == "rebuilt"
    assert index.search(label="truck") == []


def test_queue_over_threshold_alerts_and_still_uploads(tmp_path):
    spool = tmp_path / "spool"
    ready = _bundle(tmp_path).close()
    outcome = drain(spool, ScriptedTransport(), alert_bytes=0)
    assert "queue_over_threshold" in outcome["alerts"]
    assert outcome["committed"]
    assert not ready.exists()


def test_auth_full_and_interrupted_uploads_do_not_drop_the_only_copy(tmp_path):
    spool = tmp_path / "spool"
    ready = _bundle(tmp_path).close()
    auth = drain(spool, ScriptedTransport(auth=True))
    assert "auth_expired" in auth["alerts"]
    assert ready.exists()
    full = drain(spool, ScriptedTransport(full=True))
    assert "storage_full" in full["alerts"]
    assert ready.exists()
    interrupted = drain(spool, ScriptedTransport(interrupt_after=0), attempts=2, sleeper=lambda _s: None)
    assert interrupted["errors"]
    assert interrupted["errors"][0]["kept"] is True
    assert ready.exists()
    with pytest.raises(Exception):
        refuse_remote_delete()


def test_rclone_commands_copy_and_cryptcheck_without_a_token():
    args = copy_args("/tmp/events.jsonl.gz.enc", "visioncrypt:sessions/2026/11/16/abc/events.jsonl.gz.enc")
    assert args[0] == "rclone"
    assert "sync" not in args
    assert "copyto" in args
    check = cryptcheck_args("/tmp/chunk", "visioncrypt:sessions/abc")
    assert check[-1] == "--one-way"
    assert "cryptcheck" in check
    from cloudos.glasses.archive.rclone import RcloneTransport

    with pytest.raises(Exception):
        RcloneTransport("visioncrypt:token=ya29.secret")
    seen = {}

    def runner(argv):
        seen["argv"] = argv
        return {"code": 0, "stdout": b"", "stderr": b""}

    transport = RcloneTransport("visioncrypt:", runner=runner)
    transport.cryptcheck("/tmp/chunk", "sessions/abc")
    assert seen["argv"][-1] == "--one-way"
    report = probe()
    assert report["windows_workspace_inspected"] is False
    assert report["authenticated_upload"] is False
    assert report["paid_inference"] is False


def test_drain_job_needs_setup_and_does_not_call_the_network(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("archive test tried to use the network")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.delenv("GLASSES_ARCHIVE_RCLONE_REMOTE", raising=False)
    monkeypatch.delenv("GLASSES_ARCHIVE_PASSPHRASE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from cloudos.worker.handlers import HANDLERS, dispatch

    assert "glasses.archive.drain" in HANDLERS
    result = dispatch({"type": "glasses.archive.drain", "payload": {}})
    assert result["status"] == "needs_setup"
    assert result["paid_inference"] is False
    assert result["authenticated_upload"] is False
