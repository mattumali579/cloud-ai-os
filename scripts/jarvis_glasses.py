"""Run the private Visor VDK bridge for Ray-Ban Meta glasses."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import uuid
from contextlib import suppress
from pathlib import Path

import visor_kit

from cloudos.glasses.credentials import (
    CredentialError,
    ensure_audit_database_url_env,
    load_token,
    save_audit_database_url,
    save_token,
)
from cloudos.glasses.audit import enqueue_activity_event
from cloudos.glasses.jarvis import BridgeConfig, classify_command, command_body, default_config_path, default_search_roots, handle_command, jarvis_hotword_command, new_conversation_id, normalize_voice_text, resolve_session_followup, voice_request_phrases
from cloudos.worker.task_exec import _run_codex, _workspace_root


# Voice operator work often needs multi-step GUI verification; keep this aligned
# with task.run's practical desktop budget so Codex is not killed mid-task.
GENERIC_TASK_TIMEOUT_S = 900
NATIVE_COMMAND_TIMEOUT_S = 90
GROK_AI_TIMEOUT_S = 120
SPEECH_TIMEOUT_S = 8
DEDUP_WINDOW_S = 6
TRANSCRIPT_PRIORITY_DELAY_S = 0.25
TRANSCRIPT_RETRY_S = 3
TRANSCRIPT_BLUETOOTH_BACKOFF_S = 30
# Error 8 = Android SpeechRecognizer ERROR_RECOGNIZER_BUSY; retrying in 2s just spins.
TRANSCRIPT_SPEECH_ERROR_BACKOFF_S = 20


def wants_jarvis_off(text: str) -> bool:
    normalized = normalize_voice_text(text)
    return any(
        phrase in normalized
        for phrase in (
            "turn off",
            "stop listening",
            "go offline",
            "go quiet",
            "that is all",
            "that's all",
        )
    )


def _log_event(correlation_id: str, event: str, *, error: bool = False, **fields) -> None:
    record = {"correlation_id": correlation_id, "event": event, **fields}
    print(
        json.dumps(record, ensure_ascii=True, default=str, sort_keys=True),
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )
    enqueue_activity_event(correlation_id, event, error=error, **fields)


def new_session() -> dict:
    return {
        "active": False,
        "conversation_id": new_conversation_id(),
        "last_actions": [],
        "pending_plan": [],
        "task_lock": asyncio.Lock(),
        "recent_commands": {},
        "restart_transcripts": asyncio.Event(),
        "tasks": set(),
    }


def claim_command(
    session: dict,
    text: str,
    config: BridgeConfig,
    source: str,
    source_id: str = "",
) -> dict:
    """Assign an event ID and accept only one copy of a cross-source utterance."""
    correlation_id = uuid.uuid4().hex[:12]
    task = command_body(text, config.phrase).strip()
    key = normalize_voice_text(task) or "__wake__"
    classification = "off" if wants_jarvis_off(text) else ("wake" if not task else classify_command(task))
    _log_event(
        correlation_id,
        "received",
        source=source,
        source_id=source_id,
        text=text,
        conversation_id=session.get("conversation_id"),
        request_id=source_id or correlation_id,
        tool_call_id=source_id if source == "assistant.tools" else "",
    )
    _log_event(
        correlation_id,
        "classified",
        classification=classification,
        command=task,
        conversation_id=session.get("conversation_id"),
        request_id=source_id or correlation_id,
        tool_call_id=source_id if source == "assistant.tools" else "",
    )
    now = asyncio.get_running_loop().time()
    previous = session["recent_commands"].get(key)
    if previous and now - previous["time"] < DEDUP_WINDOW_S:
        _log_event(
            correlation_id,
            "deduplicated",
            duplicate_of=previous["correlation_id"],
            source=source,
        )
        return {
            "accepted": False,
            "correlation_id": correlation_id,
            "duplicate_of": previous["correlation_id"],
            "classification": classification,
            "command": task,
        }
    session["recent_commands"][key] = {"time": now, "correlation_id": correlation_id}
    _log_event(
        correlation_id,
        "accepted",
        source=source,
        conversation_id=session.get("conversation_id"),
        request_id=source_id or correlation_id,
        tool_call_id=source_id if source == "assistant.tools" else "",
    )
    return {
        "accepted": True,
        "correlation_id": correlation_id,
        "duplicate_of": None,
        "classification": classification,
        "command": task,
    }


async def _capture_one_visor_frame(adapter, *, prefer_live: bool = True):
    """Capture one analysis frame. Never uses camera.photo gallery import."""
    from cloudos.glasses.vision.capture import CaptureAuthorizationError

    live_photo = getattr(adapter._visor, "supports", lambda _n: False)("camera.live.photo")
    if live_photo:
        return await adapter.capture_live_photo_async()
    raise CaptureAuthorizationError(
        "BLOCKED: Visor session lacks camera.live.photo. "
        "Jarvis will not call camera.photo (that saves a gallery still). "
        "Grant scope camera, wear the glasses, then retry."
    )


async def _run_with_live_feed(visor, work):
    """Keep camera.live flowing (must be read or Visor kills it after ~5s) while work() runs."""
    from cloudos.glasses.vision.capture import CaptureAuthorizationError

    stop = asyncio.Event()
    ready = asyncio.Event()
    stats = {"samples": 0, "error": ""}

    async def drain() -> None:
        try:
            async for header, payload in visor.feed(
                "camera.live",
                {"quality": "low", "frameRate": 2, "audio": False},
            ):
                stats["samples"] += 1
                if stats["samples"] == 1:
                    print(
                        "Vision live video flowing: "
                        f"kind={header.get('kind')} codec={header.get('codec')} "
                        f"key={header.get('key')} bytes={len(payload)}",
                        flush=True,
                    )
                    ready.set()
                if stop.is_set():
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stats["error"] = f"{type(exc).__name__}: {exc}"
            print(f"Vision live drain ended: {stats['error']}", flush=True)
            ready.set()

    if not getattr(visor, "supports", lambda _n: False)("camera.live"):
        raise CaptureAuthorizationError("BLOCKED: Visor session lacks camera.live feed.")

    print("Vision live feed starting (low/2fps); draining samples so Live stays up.", flush=True)
    task = asyncio.create_task(drain(), name="jarvis-vision-live-drain")
    try:
        try:
            await asyncio.wait_for(ready.wait(), timeout=90)
        except TimeoutError as exc:
            raise CaptureAuthorizationError(
                "Live feed opened but no video samples arrived in 90s. "
                "Check phone Wi-Fi (Wi-Fi Direct), Nearby devices permission, "
                "and that Tailscale/VPN is not blocking glasses Live."
            ) from exc
        if stats["samples"] == 0 and stats["error"]:
            raise CaptureAuthorizationError(f"Live feed failed before video: {stats['error']}")
        # Let Live stabilize past the first config/keyframe before live.photo.
        await asyncio.sleep(0.75)
        return await work()
    finally:
        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        print(f"Vision live feed closed after {stats['samples']} sample(s).", flush=True)


async def _capture_live_burst(visor, adapter, *, frames: int = 6, interval_s: float = 0.5):
    """Short low-FPS live burst for tracking via camera.live + live.photo (not gallery stills)."""
    from cloudos.glasses.vision.capture import CaptureAuthorizationError
    from cloudos.glasses.vision.safety import SafetyGate, SafetyStop

    gate = SafetyGate()

    async def _burst():
        out = []
        for index in range(frames):
            status = await asyncio.wait_for(visor.call("glasses.status"), timeout=5)
            try:
                gate.check(status)
            except SafetyStop as stop_exc:
                print(f"Vision live burst stopped for safety: {stop_exc}", flush=True)
                break
            frame = await _capture_one_visor_frame(adapter, prefer_live=True)
            out.append(frame)
            print(
                f"Vision live burst frame {index + 1}/{frames} source={frame.source} "
                f"hw={frame.hardware_verified}",
                flush=True,
            )
            await asyncio.sleep(interval_s)
        if not out:
            raise CaptureAuthorizationError(
                "Live tracking captured no frames (camera blocked, Live failed, or stopped for safety)."
            )
        return out

    return await _run_with_live_feed(visor, _burst)


async def _capture_single_with_live(visor, adapter):
    """Single authorized frame: brief Live session + live.photo (network base64)."""

    async def _one():
        return await _capture_one_visor_frame(adapter, prefer_live=True)

    return await _run_with_live_feed(visor, _one)


# Live-tracking session: NO camera.live feed over the Jarvis WebSocket (that floods
# keepalive and makes Visor hang on "thinking"). Visor Live View on the phone keeps
# Live running; Jarvis only takes fast live.photo snapshots when asked.
_LIVE_TRACK: dict | None = None
LIVE_PHOTO_TIMEOUT_S = 10.0


def live_track_active() -> bool:
    return bool(_LIVE_TRACK and _LIVE_TRACK.get("active"))


async def _snapshot_live_photo(adapter, *, timeout_s: float = LIVE_PHOTO_TIMEOUT_S):
    """Bounded live.photo so questions never hang forever."""
    from cloudos.glasses.vision.capture import CaptureAuthorizationError

    try:
        return await asyncio.wait_for(adapter.capture_live_photo_async(), timeout=timeout_s)
    except TimeoutError as exc:
        raise CaptureAuthorizationError(
            "Live snapshot timed out. Keep Visor Live View open on the phone and try again."
        ) from exc
    except Exception as exc:
        detail = str(exc).lower()
        if "busy" in detail and "live" in detail:
            raise CaptureAuthorizationError(
                "Visor Live View is not running."
            ) from exc
        raise


async def _stop_live_track_session(reason: str = "stopped") -> str:
    global _LIVE_TRACK
    if not _LIVE_TRACK:
        return "Tracking was not running."
    _LIVE_TRACK["active"] = False
    label = (_LIVE_TRACK.get("stats") or {}).get("last_label")
    _LIVE_TRACK = None
    print(f"Vision live tracking ended ({reason}).", flush=True)
    extra = f" Last seen {label}." if label else ""
    return f"Live tracking stopped.{extra} Say start live tracking to resume."


async def _capture_vision_frame(visor, adapter):
    """Prefer phone Live View snapshot; otherwise brief bridge-owned Live + live.photo."""
    from cloudos.glasses.vision.capture import CaptureAuthorizationError

    try:
        return await _snapshot_live_photo(adapter, timeout_s=LIVE_PHOTO_TIMEOUT_S)
    except CaptureAuthorizationError as snap_exc:
        detail = str(snap_exc).lower()
        if "live view is not running" in detail or "live is not running" in detail or "not running" in detail:
            print(
                f"Vision snapshot unavailable ({snap_exc}); starting brief camera.live for one frame.",
                flush=True,
            )
            return await _capture_single_with_live(visor, adapter)
        raise


async def _ensure_live_track_session(visor, adapter, glasses_status: dict | None) -> str:
    """Arm Q&A mode. Prefer phone Live View; else brief Live per ask (no long HEVC drain)."""
    global _LIVE_TRACK
    from cloudos.glasses.vision.capture import CaptureAuthorizationError
    from cloudos.glasses.vision.detector import BlobClassDetector
    from cloudos.glasses.vision.safety import SafetyGate

    if live_track_active():
        return (
            "Live tracking is already on. "
            "Ask what am I looking at anytime. Say stop tracking when done."
        )

    print(
        "Vision live tracking arming. "
        "Will use Live View snapshots when open, else brief camera.live per ask.",
        flush=True,
    )
    try:
        SafetyGate().check(glasses_status)
    except Exception as exc:
        return str(exc)

    try:
        frame = await _capture_vision_frame(visor, adapter)
    except CaptureAuthorizationError as exc:
        return str(exc)
    except Exception as exc:
        return (
            f"Could not capture from glasses ({type(exc).__name__}: {exc}). "
            "Wear the glasses, keep Visor connected, then retry."
        )

    detections = BlobClassDetector().detect(frame)
    label = detections[0].label if detections else "the scene"
    conf = int(round((detections[0].confidence if detections else 0) * 100))
    _LIVE_TRACK = {
        "visor": visor,
        "adapter": adapter,
        "active": True,
        "started": asyncio.get_running_loop().time(),
        "max_s": 180.0,
        "stats": {"last_label": label, "asks": 0},
        "glasses_status": glasses_status,
        "mode": "brief_live" if frame.source == "visor_camera.live.photo" else "phone_live",
    }
    return (
        f"Live tracking is on — reading live frames, not saving photos. "
        f"I currently see {label}"
        + (f" at about {conf} percent. " if detections else ". ")
        + "Ask what am I looking at anytime. Say stop tracking when done."
    )


async def _live_track_describe_now() -> str:
    from cloudos.glasses.vision.capture import CaptureAuthorizationError
    from cloudos.glasses.vision.detector import BlobClassDetector
    from cloudos.glasses.vision.safety import SafetyGate, SafetyStop

    if not live_track_active() or _LIVE_TRACK is None:
        raise CaptureAuthorizationError(
            "Live tracking is not armed. Say start live tracking, then ask again."
        )
    started = float(_LIVE_TRACK.get("started") or 0)
    max_s = float(_LIVE_TRACK.get("max_s") or 180)
    if asyncio.get_running_loop().time() - started >= max_s:
        await _stop_live_track_session("time-bound")
        return "Live tracking hit the 3-minute safety limit and stopped. Say start live tracking to resume."

    adapter = _LIVE_TRACK["adapter"]
    visor = _LIVE_TRACK["visor"]
    status = await asyncio.wait_for(visor.call("glasses.status"), timeout=5)
    try:
        SafetyGate().check(status)
    except SafetyStop as exc:
        await _stop_live_track_session("thermal")
        return str(exc)
    try:
        frame = await _capture_vision_frame(visor, adapter)
    except CaptureAuthorizationError as exc:
        return str(exc)
    detections = BlobClassDetector().detect(frame)
    _LIVE_TRACK["stats"]["last_label"] = detections[0].label if detections else None
    _LIVE_TRACK["stats"]["asks"] = int(_LIVE_TRACK["stats"].get("asks") or 0) + 1
    speech = BlobClassDetector().speak(detections)
    return f"{speech} Tracking is still on."


async def _prepare_vision_runtime(
    visor,
    glasses_status: dict | None,
    *,
    need_frame: bool,
    mode: str = "single_shot",
) -> None:
    """Attach issue #25 vision pipeline. Prefer Visor camera.photo; never fake hardware frames."""
    from cloudos.glasses.vision import VisionPipeline, get_runtime_pipeline, set_runtime_pipeline
    from cloudos.glasses.vision.capture import (
        CaptureAuthorizationError,
        FrameSequenceCaptureAdapter,
        InMemoryCaptureAdapter,
        VisorCameraCaptureAdapter,
    )
    from cloudos.glasses.vision.detector import BlobClassDetector
    from cloudos.glasses.vision.faces import FaceRegistry
    from cloudos.glasses.vision.safety import SafetyGate

    faces_enabled = os.environ.get("JARVIS_FACE_RECOGNITION", "").strip().lower() in {"1", "true", "yes", "on"}
    faces_root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "CloudAIOS" / "jarvis-faces"
    adapter = VisorCameraCaptureAdapter(visor, authorized=True)
    policy = VisorCameraCaptureAdapter.session_policy()
    print(
        "Vision capture policy: "
        f"mode={mode} max_fps={policy['max_fps']} "
        f"camera.photo={adapter.camera_photo_supported()} "
        f"camera.live={adapter.camera_live_supported()} "
        f"face_recognition={'on' if faces_enabled else 'off'} need_frame={need_frame}",
        flush=True,
    )
    frame = None
    frames: list = []
    if need_frame:
        try:
            if mode == "live_track":
                frames = await _capture_live_burst(visor, adapter, frames=6, interval_s=0.5)
                if not frames:
                    raise CaptureAuthorizationError(
                        "Live tracking stopped before any frame (thermal/battery) or camera was unavailable."
                    )
                capture = FrameSequenceCaptureAdapter(frames=frames, authorized=True)
                frame = frames[0]
            else:
                # Live View snapshot or brief camera.live + live.photo only — never gallery stills.
                frame = await _capture_vision_frame(visor, adapter)
                capture = InMemoryCaptureAdapter(frame=frame, authorized=True)
        except CaptureAuthorizationError as exc:
            # Python 3.11+ clears `exc` after the except block; bind the message now.
            blocked_msg = str(exc)
            print(f"Vision capture blocked: {blocked_msg}", flush=True)

            class _Blocked:
                def capture_photo(self):
                    raise CaptureAuthorizationError(blocked_msg)

                def all_frames(self):
                    raise CaptureAuthorizationError(blocked_msg)

            capture = _Blocked()
        except Exception as exc:
            blocked_msg = f"Visor camera capture failed: {type(exc).__name__}: {exc}"
            print(blocked_msg, flush=True)

            class _Blocked:
                def capture_photo(self):
                    raise CaptureAuthorizationError(blocked_msg)

                def all_frames(self):
                    raise CaptureAuthorizationError(blocked_msg)

            capture = _Blocked()
    else:
        existing = get_runtime_pipeline()
        if existing is not None:
            existing.glasses_status = glasses_status
            existing.faces = FaceRegistry(faces_root, enabled=faces_enabled)
            set_runtime_pipeline(existing)
            return

        class _NoFrame:
            def capture_photo(self):
                raise CaptureAuthorizationError("No camera frame requested for this vision command.")

        capture = _NoFrame()
    pipe = VisionPipeline(
        capture=capture,
        detector=BlobClassDetector(),
        faces=FaceRegistry(faces_root, enabled=faces_enabled),
        safety=SafetyGate(),
        glasses_status=glasses_status,
    )
    if frame is not None:
        pipe._visible["last_source"] = frame.source
        pipe._visible["hardware_verified"] = frame.hardware_verified
        pipe._visible["burst_frames"] = len(frames) if frames else 1
    set_runtime_pipeline(pipe)


async def answer_text(visor, config: BridgeConfig, text: str, session: dict | None = None) -> str:
    """Use fast native handlers first, then Codex for general desktop tasks."""
    resolved = resolve_session_followup(text, config.phrase, session)
    task = resolved or command_body(text, config.phrase).strip()
    classification = classify_command(task) if task else "wake"
    if classification != "operator":
        native_timeout = GROK_AI_TIMEOUT_S if classification == "grok_ai" else NATIVE_COMMAND_TIMEOUT_S
        try:
            current = await asyncio.wait_for(visor.call("glasses.status"), timeout=5)
            if classification.startswith("vision_"):
                from cloudos.glasses.vision.capture import VisorCameraCaptureAdapter

                adapter = VisorCameraCaptureAdapter(visor, authorized=True)
                # Persistent live tracking: keep Live on; answer questions without tearing it down.
                if classification == "vision_track":
                    native_timeout = max(native_timeout, 120)
                    return await asyncio.wait_for(
                        _ensure_live_track_session(visor, adapter, current),
                        timeout=native_timeout,
                    )
                if classification == "vision_track_stop":
                    return await _stop_live_track_session("user-stop")
                if classification == "vision_describe":
                    # Brief camera.live + HEIC live.photo can take ~30–45s when Live View is off.
                    native_timeout = max(native_timeout, 75)
                    if live_track_active():
                        return await asyncio.wait_for(_live_track_describe_now(), timeout=native_timeout)
                    # Auto-arm: Live View snapshot if open, else brief bridge-owned Live.
                    try:
                        armed = await asyncio.wait_for(
                            _ensure_live_track_session(visor, adapter, current),
                            timeout=native_timeout,
                        )
                        # If arming already described the scene, return that; else snapshot now.
                        if "I currently see" in armed or "Live tracking is on" in armed:
                            if "Ask what am I looking at" in armed:
                                follow = await asyncio.wait_for(_live_track_describe_now(), timeout=native_timeout)
                                return follow
                            return armed
                    except Exception as exc:
                        print(f"Vision auto-arm failed: {type(exc).__name__}: {exc}", flush=True)
                # Single-shot describe / face paths still use prepare + sync pipeline.
                need_frame = classification not in {"vision_face_remove"}
                mode = "single_shot"
                await _prepare_vision_runtime(visor, current, need_frame=need_frame, mode=mode)
            return await asyncio.wait_for(
                asyncio.to_thread(handle_command, text, config, current, session),
                timeout=native_timeout,
            )
        except TimeoutError:
            print(
                f"Native command timed out after {native_timeout}s ({classification})",
                file=sys.stderr,
                flush=True,
            )
            return (
                "That local command is still running past the safe wait window. "
                "Ask for task status, or say stop Jarvis if you want it cancelled."
            )
        except Exception as exc:
            print(f"Native command failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            return "I could not complete that local command. Check the Jarvis log on the mini PC."
    task += (
        "\n\nFor computer interaction, use the Windows-MCP tools. Start with Snapshot or Screenshot, "
        "then use App, Click, Type, Scroll, Shortcut, Wait, or Scrape as needed. "
        "Verify the requested result from the visible UI before reporting success. "
        "Do not claim a change happened unless you confirmed it. "
        "Prefer finishing the full request over stopping early."
    )
    try:
        workspace = _workspace_root() / "Jarvis-Voice" / uuid.uuid4().hex
        workspace.mkdir(parents=True, exist_ok=True)
        result = await asyncio.to_thread(_run_codex, task, workspace, GENERIC_TASK_TIMEOUT_S)
        return str(result.get("output", "")).strip() or "The computer task finished without a spoken result."
    except Exception as exc:
        print(f"Computer task failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        detail = str(exc).lower()
        if "timed out" in detail:
            minutes = max(1, GENERIC_TASK_TIMEOUT_S // 60)
            return (
                f"I hit the {minutes}-minute limit before that computer task finished, "
                "so I am not claiming it completed. Ask for task status, or say stop Jarvis to cancel."
            )
        if "usage limit" in detail or "quota" in detail:
            return "I cannot run the general computer agent right now because the Codex subscription usage limit is reached."
        return "I could not complete that computer task. Check the Jarvis log on the mini PC."


def _log_result(
    correlation_id: str,
    answer: str,
    *,
    conversation_id: str = "",
    request_id: str = "",
    job_id: str = "",
    tool_call_id: str = "",
) -> None:
    _log_event(
        correlation_id,
        "action_completed",
        result=answer,
        conversation_id=conversation_id,
        request_id=request_id or correlation_id,
        job_id=job_id,
        tool_call_id=tool_call_id,
    )
    lower = answer.lower()
    unverified = any(
        phrase in lower
        for phrase in ("could not", "did not answer", "failed", "not claiming", "without a spoken result")
    )
    _log_event(
        correlation_id,
        "result_verification_failed" if unverified else "result_verified",
        result=answer,
        conversation_id=conversation_id,
        request_id=request_id or correlation_id,
        job_id=job_id,
        tool_call_id=tool_call_id,
    )


async def _speak(
    visor,
    correlation_id: str,
    text: str,
    *,
    channel: str = "voice.speak",
    conversation_id: str = "",
    request_id: str = "",
    job_id: str = "",
    tool_call_id: str = "",
) -> bool:
    _log_event(
        correlation_id,
        "speech_requested",
        channel=channel,
        text=text,
        conversation_id=conversation_id,
        request_id=request_id or correlation_id,
        job_id=job_id,
        tool_call_id=tool_call_id,
    )
    try:
        await asyncio.wait_for(visor.call(channel, {"text": text[:500]}), timeout=SPEECH_TIMEOUT_S)
    except Exception as exc:
        _log_event(
            correlation_id,
            "speech_failed",
            error=True,
            channel=channel,
            error_type=type(exc).__name__,
            detail=str(exc),
            conversation_id=conversation_id,
            request_id=request_id or correlation_id,
            job_id=job_id,
            tool_call_id=tool_call_id,
        )
        return False
    _log_event(
        correlation_id,
        "speech_acknowledged",
        channel=channel,
        conversation_id=conversation_id,
        request_id=request_id or correlation_id,
        job_id=job_id,
        tool_call_id=tool_call_id,
    )
    return True


async def _reply(
    visor,
    correlation_id: str,
    request_id: str,
    text: str,
    *,
    listen: bool = False,
    conversation_id: str = "",
    job_id: str = "",
    tool_call_id: str = "",
) -> bool:
    _log_event(
        correlation_id,
        "speech_requested",
        channel="voice.reply",
        text=text,
        listen=listen,
        conversation_id=conversation_id,
        request_id=request_id,
        job_id=job_id,
        tool_call_id=tool_call_id,
    )
    try:
        await asyncio.wait_for(
            visor.call(
                "voice.reply",
                {"request": request_id, "text": text[:500], "listen": bool(listen)},
            ),
            timeout=SPEECH_TIMEOUT_S,
        )
    except Exception as exc:
        _log_event(
            correlation_id,
            "speech_failed",
            error=True,
            channel="voice.reply",
            error_type=type(exc).__name__,
            detail=str(exc),
            conversation_id=conversation_id,
            request_id=request_id,
            job_id=job_id,
            tool_call_id=tool_call_id,
        )
        return False
    _log_event(
        correlation_id,
        "speech_acknowledged",
        channel="voice.reply",
        conversation_id=conversation_id,
        request_id=request_id,
        job_id=job_id,
        tool_call_id=tool_call_id,
    )
    return True


async def finish_computer_task(
    visor,
    config: BridgeConfig,
    session: dict,
    text: str,
    correlation_id: str,
) -> None:
    """Serialize desktop tasks and speak the verified result when each finishes."""
    conversation_id = str(session.get("conversation_id") or "")
    job_id = f"jarvis-{correlation_id}"
    async with session["task_lock"]:
        _log_event(
            correlation_id,
            "started",
            command=command_body(text, config.phrase).strip(),
            conversation_id=conversation_id,
            request_id=correlation_id,
            job_id=job_id,
        )
        answer = await answer_text(visor, config, text, session)
        _log_result(
            correlation_id,
            answer,
            conversation_id=conversation_id,
            request_id=correlation_id,
            job_id=job_id,
        )
        if session.get("active", False):
            await _speak(
                visor,
                correlation_id,
                answer,
                conversation_id=conversation_id,
                request_id=correlation_id,
                job_id=job_id,
            )
        else:
            _log_event(
                correlation_id,
                "speech_skipped",
                reason="private_session_inactive",
                conversation_id=conversation_id,
                request_id=correlation_id,
                job_id=job_id,
            )


def schedule_computer_task(
    visor,
    config: BridgeConfig,
    session: dict,
    text: str,
    correlation_id: str,
) -> asyncio.Task:
    """Retain a background task and consume every terminal exception."""
    conversation_id = str(session.get("conversation_id") or "")
    task = asyncio.create_task(
        finish_computer_task(visor, config, session, text, correlation_id),
        name=f"jarvis-{correlation_id}",
    )
    session["tasks"].add(task)

    def task_done(done: asyncio.Task) -> None:
        session["tasks"].discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            _log_event(
                correlation_id,
                "background_task_cancelled",
                error=True,
                conversation_id=conversation_id,
                request_id=correlation_id,
                job_id=done.get_name(),
            )
        except Exception as exc:
            _log_event(
                correlation_id,
                "background_task_failed",
                error=True,
                error_type=type(exc).__name__,
                detail=str(exc),
                conversation_id=conversation_id,
                request_id=correlation_id,
                job_id=done.get_name(),
            )

    task.add_done_callback(task_done)
    _log_event(
        correlation_id,
        "queued",
        conversation_id=conversation_id,
        request_id=correlation_id,
        job_id=task.get_name(),
    )
    return task


def cancel_computer_tasks(session: dict, correlation_id: str) -> str:
    """Cancel in-flight desktop background tasks and report what remained."""
    active = [task for task in list(session.get("tasks", set())) if not task.done()]
    if not active:
        return "There was no active computer task to cancel."
    for task in active:
        task.cancel()
    _log_event(
        correlation_id,
        "cancel_requested",
        cancelled_count=len(active),
        task_names=[task.get_name() for task in active],
        conversation_id=session.get("conversation_id"),
        request_id=correlation_id,
        job_id=active[0].get_name() if active else "",
    )
    return f"Cancelled {len(active)} computer task(s). Incomplete steps were stopped."

async def serve_named_requests(visor, config: BridgeConfig, session: dict) -> None:
    # Visor requires concrete routing phrases (no wildcard). Register bare Jarvis
    # and Hey Jarvis so either spoken form is claimed by this program.
    phrases = voice_request_phrases(config.phrase)
    print(f"Jarvis voice phrases: {', '.join(phrases)}.", flush=True)
    async for request in visor.subscribe("voice.requests", {"phrases": phrases}):
        text = str(request.get("text", ""))
        claim = claim_command(
            session,
            text,
            config,
            "voice.requests",
            str(request.get("id", "")),
        )
        correlation_id = claim["correlation_id"]
        request_id = str(request.get("id", "") or correlation_id)
        if not claim["accepted"]:
            await _reply(
                visor,
                correlation_id,
                request_id,
                "I am already handling that.",
                conversation_id=str(session.get("conversation_id") or ""),
            )
            continue
        _log_event(
            correlation_id,
            "started",
            command=claim["command"],
            conversation_id=session.get("conversation_id"),
            request_id=request_id,
        )
        if claim["classification"] == "off":
            session["active"] = False
            session["conversation_id"] = new_conversation_id()
            session["last_actions"] = []
            session["pending_plan"] = []
            answer = "Going quiet."
        elif claim["classification"] == "wake":
            session["active"] = True
            if not session.get("conversation_id"):
                session["conversation_id"] = new_conversation_id()
            answer = "Yes?"
        else:
            if claim["classification"] == "operator_cancel":
                session["active"] = False
            else:
                session["active"] = True
            if claim["classification"] == "operator" and str(classify_command(claim["command"] or text)).startswith("vision_"):
                # Safety net: never send glasses vision to Codex/Windows-MCP.
                answer = await answer_text(visor, config, text, session)
                _log_result(
                    correlation_id,
                    answer,
                    conversation_id=str(session.get("conversation_id") or ""),
                    request_id=request_id,
                )
            elif claim["classification"] == "operator":
                schedule_computer_task(visor, config, session, text, correlation_id)
                answer = "Working on it. I will tell you when it is done."
            elif claim["classification"] == "operator_cancel":
                answer = cancel_computer_tasks(session, correlation_id)
                job_answer = await asyncio.to_thread(handle_command, text, config, None, session)
                if "Cancelled the task" in job_answer:
                    answer = f"{answer} {job_answer}".strip()
                elif answer.startswith("There was no active") and "no recent task" in job_answer.lower():
                    answer = "There was no active computer task to cancel."
                _log_result(
                    correlation_id,
                    answer,
                    conversation_id=str(session.get("conversation_id") or ""),
                    request_id=request_id,
                )
            else:
                if str(claim["classification"]).startswith("vision_"):
                    # Immediate spoken ack so Visor does not sit on silent "thinking".
                    await _speak(
                        visor,
                        correlation_id,
                        "Looking now.",
                        conversation_id=str(session.get("conversation_id") or ""),
                        request_id=request_id,
                    )
                answer = await answer_text(visor, config, text, session)
                _log_result(
                    correlation_id,
                    answer,
                    conversation_id=str(session.get("conversation_id") or ""),
                    request_id=request_id,
                )
        if claim["classification"] in {"off", "wake"}:
            _log_result(
                correlation_id,
                answer,
                conversation_id=str(session.get("conversation_id") or ""),
                request_id=request_id,
            )
        # A long-running computer job continues independently and reports its
        # result through voice.speak. Keeping voice.reply in listen mode here
        # leaves Visor's assistant audio session open like a call; Meta then
        # rejects the next computer-tool request with "hang up the call".
        # Release that audio session while preserving the Jarvis conversation
        # and background task so a new named request can resume it later.
        listen = bool(session.get("active", False)) and claim["classification"] not in {"off", "operator"}
        _log_event(
            correlation_id,
            "session_state",
            conversation_id=session.get("conversation_id"),
            active=session.get("active"),
            listen=listen,
            actions=len(session.get("last_actions", [])),
            request_id=request_id,
        )
        await _reply(
            visor,
            correlation_id,
            request_id,
            answer,
            listen=listen,
            conversation_id=str(session.get("conversation_id") or ""),
        )
        if listen and session.get("restart_transcripts") is not None:
            session["restart_transcripts"].set()
            _log_event(correlation_id, "transcript_rearm_requested", conversation_id=session.get("conversation_id"))


async def _next_transcript_or_rearm(stream, rearm: asyncio.Event):
    next_event = asyncio.create_task(stream.__anext__())
    rearm_wait = asyncio.create_task(rearm.wait())
    done, _ = await asyncio.wait(
        {next_event, rearm_wait},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if rearm_wait in done:
        next_event.cancel()
        with suppress(asyncio.CancelledError, StopAsyncIteration):
            await next_event
        return None
    rearm_wait.cancel()
    with suppress(asyncio.CancelledError):
        await rearm_wait
    return next_event.result()


async def serve_private_hotword(visor, config: BridgeConfig, session: dict) -> None:
    """Listen to glasses transcripts while Visor is visible on the phone."""
    while True:
        stream = None
        retry_delay_s = TRANSCRIPT_RETRY_S
        try:
            print("Private hotword ready: Hey Jarvis or Jarvis.", flush=True)
            stream = await visor.open(
                "voice.transcripts",
                {"language": "en-US", "partials": False},
            )
            while True:
                try:
                    transcript = await _next_transcript_or_rearm(
                        stream,
                        session["restart_transcripts"],
                    )
                except StopAsyncIteration:
                    break
                if transcript is None:
                    session["restart_transcripts"].clear()
                    retry_delay_s = 0
                    print("Private transcript listener rearmed after named reply.", flush=True)
                    break
                if transcript.get("partial"):
                    continue
                text = str(transcript.get("text", ""))
                await asyncio.sleep(TRANSCRIPT_PRIORITY_DELAY_S)
                command = jarvis_hotword_command(text, config.phrase)
                conversation_id = str(session.get("conversation_id") or "")
                _log_event(
                    "transcript",
                    "transcript_heard",
                    text=text,
                    hotword=command is not None,
                    session_active=bool(session.get("active")),
                    conversation_id=conversation_id,
                )
                if command == "":
                    claim = claim_command(
                        session,
                        text,
                        config,
                        "voice.transcripts",
                        str(transcript.get("id", "")),
                    )
                    if not claim["accepted"]:
                        continue
                    session["active"] = True
                    request_id = str(transcript.get("id", "") or claim["correlation_id"])
                    _log_event(
                        claim["correlation_id"],
                        "started",
                        command="",
                        conversation_id=conversation_id,
                        request_id=request_id,
                    )
                    _log_result(
                        claim["correlation_id"],
                        "Yes?",
                        conversation_id=conversation_id,
                        request_id=request_id,
                    )
                    await _speak(
                        visor,
                        claim["correlation_id"],
                        "Yes?",
                        conversation_id=conversation_id,
                        request_id=request_id,
                    )
                    continue
                if command is None:
                    if not session.get("active", False) or normalize_voice_text(text) in {"yes", "yes?"}:
                        continue
                    command = text
                routed_text = f"{config.phrase} {command}"
                claim = claim_command(
                    session,
                    routed_text,
                    config,
                    "voice.transcripts",
                    str(transcript.get("id", "")),
                )
                if not claim["accepted"]:
                    continue
                correlation_id = claim["correlation_id"]
                request_id = str(transcript.get("id", "") or correlation_id)
                conversation_id = str(session.get("conversation_id") or "")
                _log_event(
                    correlation_id,
                    "started",
                    command=claim["command"],
                    conversation_id=conversation_id,
                    request_id=request_id,
                )
                if claim["classification"] == "off":
                    session["active"] = False
                    _log_result(
                        correlation_id,
                        "Going quiet.",
                        conversation_id=conversation_id,
                        request_id=request_id,
                    )
                    await _speak(
                        visor,
                        correlation_id,
                        "Going quiet.",
                        conversation_id=conversation_id,
                        request_id=request_id,
                    )
                    continue
                if claim["classification"] == "operator_cancel":
                    session["active"] = False
                else:
                    session["active"] = True
                if claim["classification"] == "operator":
                    schedule_computer_task(visor, config, session, routed_text, correlation_id)
                    await _speak(
                        visor,
                        correlation_id,
                        "Working on it. I will tell you when it is done.",
                        conversation_id=conversation_id,
                        request_id=request_id,
                    )
                elif claim["classification"] == "operator_cancel":
                    answer = cancel_computer_tasks(session, correlation_id)
                    job_answer = await asyncio.to_thread(handle_command, routed_text, config, None, session)
                    if "Cancelled the task" in job_answer:
                        answer = f"{answer} {job_answer}".strip()
                    elif answer.startswith("There was no active") and "no recent task" in job_answer.lower():
                        answer = "There was no active computer task to cancel."
                    _log_result(
                        correlation_id,
                        answer,
                        conversation_id=conversation_id,
                        request_id=request_id,
                    )
                    await _speak(
                        visor,
                        correlation_id,
                        answer,
                        conversation_id=conversation_id,
                        request_id=request_id,
                    )
                else:
                    answer = await answer_text(visor, config, routed_text, session)
                    _log_result(
                        correlation_id,
                        answer,
                        conversation_id=conversation_id,
                        request_id=request_id,
                    )
                    await _speak(
                        visor,
                        correlation_id,
                        answer,
                        conversation_id=conversation_id,
                        request_id=request_id,
                    )
        except visor_kit.VisorError as exc:
            # Missing/temporarily unavailable microphone access is expected
            # while Visor is backgrounded or before the scope is granted. Keep
            # the normal named-request bridge alive and retry quietly.
            detail = str(exc).lower()
            print(f"Private hotword paused: {exc}; keep Visor visible to resume.", flush=True)
            _log_event(
                "transcript",
                "private_transcript_paused",
                error=True,
                error_type=type(exc).__name__,
                detail=str(exc),
            )
            if "bluetooth headset" in detail or "no bluetooth" in detail:
                # Folded / unpaired glasses are a durable state; back off hard.
                retry_delay_s = TRANSCRIPT_BLUETOOTH_BACKOFF_S
            elif "speech recognition error 8" in detail:
                # Transient STT hiccup after TTS; short non-zero retry.
                retry_delay_s = TRANSCRIPT_SPEECH_ERROR_BACKOFF_S
            else:
                retry_delay_s = TRANSCRIPT_RETRY_S
        finally:
            if stream is not None:
                await stream.aclose()
        await asyncio.sleep(retry_delay_s)



async def serve_assistant_tools(visor, config: BridgeConfig, session: dict) -> None:
    """Expose one general Windows computer task to Visor's assistant."""
    offered_tools = [{
        "name": "jarvis_computer_control",
        "description": (
            "Perform a general task on the user's Windows PC: inspect the screen, open or switch apps, "
            "click controls, type, scroll, use shortcuts, and search visible pages. This is a real computer-use "
            "agent, not an app-specific command list. Call it for computer actions, then tell the user Jarvis is "
            "working; Jarvis will speak the verified result when finished. "
            "Do NOT use this tool for glasses camera, object detection, live tracking, describe what I'm looking at, "
            "or face recognition — those are handled by Jarvis vision over voice, not Windows desktop tools."
        ),
        "schema": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The user's requested PC task."}},
            "required": ["command"],
            "additionalProperties": False,
        },
    }]
    async for call in visor.subscribe("assistant.tools", {"tools": offered_tools}):
        call_id = str(call.get("id", ""))
        if not call_id:
            continue
        command = str((call.get("arguments") or {}).get("command", "")).strip()
        if not command:
            await visor.call("assistant.tools.answer", {"call": call_id, "text": "No PC task was provided.", "error": True})
            continue
        session["active"] = True
        routed_text = f"Hey {config.phrase} {command}"
        claim = claim_command(session, routed_text, config, "assistant.tools", call_id)
        if not claim["accepted"]:
            await visor.call("assistant.tools.answer", {
                "call": call_id,
                "text": "Jarvis is already handling that PC task.",
                "error": False,
            })
            continue
        # Vision must never fall through to Codex/Windows-MCP "available tools".
        if str(claim["classification"]).startswith("vision_"):
            answer = await answer_text(visor, config, routed_text, session)
            _log_result(
                claim["correlation_id"],
                answer,
                conversation_id=str(session.get("conversation_id") or ""),
                request_id=call_id,
                tool_call_id=call_id,
            )
            await _speak(
                visor,
                claim["correlation_id"],
                answer,
                conversation_id=str(session.get("conversation_id") or ""),
                request_id=call_id,
                job_id=f"jarvis-{claim['correlation_id']}",
                tool_call_id=call_id,
            )
            await visor.call("assistant.tools.answer", {
                "call": call_id,
                "text": answer,
                "error": False,
            })
            continue
        schedule_computer_task(
            visor,
            config,
            session,
            routed_text,
            claim["correlation_id"],
        )
        await visor.call("assistant.tools.answer", {
            "call": call_id,
            "text": "Jarvis is carrying out the PC task and will speak the result when it finishes.",
            "error": False,
        })

async def serve_once(config: BridgeConfig, token: str, *, status_only: bool = False) -> None:
    async with await visor_kit.connect_network(config.visor_url, token) as visor:
        required = {"glasses.status", "voice.requests", "voice.reply"}
        unsupported = sorted(name for name in required if not visor.supports(name))
        if unsupported:
            raise RuntimeError("Visor does not support: " + ", ".join(unsupported))
        current = await visor.call("glasses.status")
        print("Connected to Visor:", current, flush=True)
        if status_only:
            print(handle_command("Jarvis status", config, current), flush=True)
            return
        print(
            f"Jarvis owns questions matching: {', '.join(voice_request_phrases(config.phrase))}.",
            flush=True,
        )
        session = new_session()
        workers = [serve_named_requests(visor, config, session)]
        if visor.supports("assistant.tools") and visor.supports("assistant.tools.answer"):
            workers.append(serve_assistant_tools(visor, config, session))
            print("Jarvis computer tool offered to Visor assistant.", flush=True)
        else:
            print("Jarvis computer tool unavailable: assistant.tools scope is not supported or granted.", file=sys.stderr, flush=True)
        if visor.supports("voice.transcripts"):
            workers.append(serve_private_hotword(visor, config, session))
            print(
                "Jarvis session follow-ups: voice.reply listen follows session.active; "
                "private transcripts enabled for active-session routing.",
                flush=True,
            )
        else:
            print(
                "Jarvis session follow-ups: voice.reply listen follows session.active; "
                "voice.transcripts unavailable on this Visor session.",
                flush=True,
            )
        cam_photo = visor.supports("camera.photo")
        cam_live = visor.supports("camera.live")
        print(
            "Vision scopes (issue #25): "
            f"camera.photo={cam_photo} camera.live={cam_live} camera.live.photo={visor.supports('camera.live.photo')}. "
            "No capture started at connect; single-shot only when user asks.",
            flush=True,
        )
        await asyncio.gather(*workers)


async def serve_forever(config: BridgeConfig, token: str) -> None:
    delay = 2
    while True:
        try:
            await serve_once(config, token)
            delay = 2
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            print(
                f"Unexpected bridge cycle cancellation; retrying in {delay} seconds.",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
        except (OSError, visor_kit.VisorError) as exc:
            print(f"Visor unavailable: {exc}; retrying in {delay} seconds.", file=sys.stderr, flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
        except Exception as exc:
            print(
                f"Unexpected bridge cycle failure: {type(exc).__name__}: {exc}; "
                f"retrying in {delay} seconds.",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


def configure(path: Path) -> None:
    phone = input("Samsung phone private IP (shown by Visor): ").strip()
    url = phone if phone.startswith("ws://") else f"ws://{phone}:7333/rpc"
    token = getpass.getpass("Visor program token (hidden): ")
    roots_text = input(f"Folders to search, separated by semicolons [{default_search_roots()[0]}]: ").strip()
    roots = tuple(Path(item.strip()).expanduser().resolve() for item in roots_text.split(";") if item.strip())
    config = BridgeConfig(url, roots or tuple(default_search_roots()))
    config.save(path)
    save_token(token)
    print(f"Saved non-secret settings to {path}")
    print("Saved the token in Windows Credential Manager.")


def configure_audit_database_url() -> None:
    """Store the private ops_audit DB URL in Credential Manager (never in Git)."""
    from_env = os.environ.get("JARVIS_AUDIT_DATABASE_URL", "").strip()
    if from_env:
        url = from_env
        print("Using JARVIS_AUDIT_DATABASE_URL from the process environment.")
    else:
        url = getpass.getpass("Paste Jarvis audit DATABASE_URL for ops_audit (hidden): ").strip()
    if not url.lower().startswith("postgres"):
        raise ValueError("Audit database URL must be a postgres connection string.")
    save_audit_database_url(url)
    os.environ["JARVIS_AUDIT_DATABASE_URL"] = url
    print("Saved JARVIS_AUDIT_DATABASE_URL in Windows Credential Manager.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("configure", "configure-audit", "token", "probe", "run", "self-test"),
    )
    parser.add_argument("--config", type=Path, default=default_config_path())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "configure":
        configure(args.config)
        return 0
    if args.command == "configure-audit":
        try:
            configure_audit_database_url()
        except (ValueError, CredentialError) as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "token":
        save_token(getpass.getpass("Paste NEW Visor program token (hidden): "))
        print("New token saved in Windows Credential Manager.")
        return 0
    if args.command == "self-test":
        config = BridgeConfig("ws://127.0.0.1:7333/rpc", tuple(default_search_roots()))
        print(handle_command("Jarvis status", config, {"paired": False, "link": "disconnected"}))
        return 0
    ensure_audit_database_url_env()
    try:
        config = BridgeConfig.load(args.config)
        token = load_token()
    except (OSError, ValueError, KeyError, CredentialError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if args.command == "run" and not os.environ.get("JARVIS_AUDIT_DATABASE_URL", "").strip():
        print(
            "JARVIS_AUDIT_DATABASE_URL is not configured; sanitized activity_log writes are disabled. "
            "Run: .\\.venv\\Scripts\\python.exe .\\scripts\\jarvis_glasses.py configure-audit",
            file=sys.stderr,
            flush=True,
        )
    try:
        asyncio.run(serve_once(config, token, status_only=True) if args.command == "probe" else serve_forever(config, token))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Bridge failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
