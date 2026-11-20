import asyncio
import json
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

from cloudos.contracts import CloudOSError
import cloudos.glasses.jarvis as jarvis_module
from cloudos.glasses.jarvis import BridgeConfig, classify_command, close_named_target, command_body, create_note, docker_control_name, find_files, glasses_status, handle_command, is_jarvis_hotword, jarvis_hotword_command, normalize_voice_text, open_named_target, operator_task_status, plan_commands, play_media_query, search_web, start_operator_task, validate_private_ws_url, voice_request_phrases
from cloudos.worker.task_exec import _run_codex
import scripts.jarvis_glasses as bridge
from scripts.jarvis_glasses import serve_assistant_tools, serve_once, wants_jarvis_off


def test_accepts_only_private_visor_urls():
    assert validate_private_ws_url("ws://192.168.1.20:7333/rpc") == "ws://192.168.1.20:7333/rpc"
    assert validate_private_ws_url("ws://100.100.10.20:7333/rpc") == "ws://100.100.10.20:7333/rpc"
    with pytest.raises(ValueError):
        validate_private_ws_url("ws://8.8.8.8:7333/rpc")
    with pytest.raises(ValueError):
        validate_private_ws_url("wss://192.168.1.20:7333/rpc")


def test_classifies_bounded_commands():
    assert classify_command("Hey Meta, ask Jarvis for status") == "status"
    assert classify_command("Jarvis find my project report") == "find"
    assert classify_command("Jarvis delete everything") == "blocked"
    assert classify_command("Jarvis please send a message") == "risk_confirm"
    assert classify_command("Jarvis click send") == "risk_confirm"
    assert classify_command("Jarvis change my account settings") == "blocked"
    assert classify_command("Jarvis weaken Windows security") == "blocked"
    assert classify_command("Jarvis check my documents") == "list_documents"
    assert classify_command("Jarvis save") == "edit_shortcut"
    assert classify_command("Jarvis open the dashboard") == "open"
    assert classify_command("Jarvis close chrome") == "close"
    assert classify_command("Jarvis quit google chrome") == "close"
    assert classify_command("Jarvis close the tab") == "close"
    assert classify_command("Jarvis take a note buy filters") == "note"


def test_private_hotword_accepts_hey_jarvis_or_bare_jarvis_at_start():
    assert is_jarvis_hotword("Hey Jarvis, open Docker") is True
    assert is_jarvis_hotword("hey jarvis run the tests") is True
    assert is_jarvis_hotword("Jarvis open Chrome") is True
    assert is_jarvis_hotword("jarvis") is True
    assert is_jarvis_hotword("someone said hey jarvis") is False
    assert is_jarvis_hotword("someone said jarvis open") is False
    assert jarvis_hotword_command("Hey Jarvis") == ""
    assert jarvis_hotword_command("Jarvis") == ""
    assert jarvis_hotword_command("Hey Jarvis, open Docker") == "open docker"
    assert jarvis_hotword_command("Jarvis open Chrome") == "open chrome"
    assert jarvis_hotword_command("open Docker") is None
    assert voice_request_phrases("jarvis") == ["Hey Jarvis", "Jarvis", "hey jarvis", "jarvis"]
    assert command_body("hey jarvis hey jarvis try again", "jarvis") == "try again"
    assert command_body("Jarvis open Chrome", "jarvis") == "open chrome"


def test_bare_jarvis_only_arms_and_never_starts_empty_job(tmp_path: Path):
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    assert handle_command("Hey Jarvis", config) == "Yes?"


def test_only_explicit_voice_phrases_turn_persistent_session_off():
    assert wants_jarvis_off("Jarvis turn off") is True
    assert wants_jarvis_off("stop listening now") is True
    assert wants_jarvis_off("open Docker") is False


def test_named_docker_navigation_routes_to_native_windows_control():
    assert docker_control_name("Hey Jarvis click Images in Docker") == "Images"
    assert docker_control_name("Jarvis go to the containers") == "Containers"
    assert docker_control_name("Jarvis click Delete") is None
    assert classify_command("Jarvis click Images in Docker") == "docker_click"
    assert classify_command("Jarvis volume up") == "volume_up"
    assert classify_command("Jarvis search the web for LSU football") == "web"
    assert classify_command("Jarvis type hello world") == "type_text"
    assert classify_command("Jarvis press enter") == "press_key"
    assert classify_command("Jarvis scroll down") == "scroll"
    assert classify_command("Jarvis skip") == "next"
    assert classify_command("Jarvis focus Chrome") == "focus_window"
    assert classify_command("Jarvis what can you do") == "help"


def test_filename_search_stays_in_approved_root(tmp_path: Path):
    (tmp_path / "LSU construction report.pdf").write_text("private contents", encoding="utf-8")
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    answer = find_files("Jarvis find construction report", config)
    assert answer == "I found 1: LSU construction report.pdf."
    assert "private contents" not in answer


def test_filename_search_ignores_voice_filler_and_returns_ranked_fallback(tmp_path: Path):
    (tmp_path / "Construction Management Resume.pdf").write_text("", encoding="utf-8")
    (tmp_path / "weekly-report.md").write_text("", encoding="utf-8")
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    answer = find_files("hey meta ask jarvis to find my construction report", config)
    assert answer.startswith("I did not find an exact filename. Closest matches are:")
    assert "Construction Management Resume.pdf" in answer


def test_status_reply_combines_glasses_and_pc(monkeypatch, tmp_path: Path):
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    monkeypatch.setattr("cloudos.glasses.jarvis.pc_status", lambda: "Jarvis PC is online.")
    answer = handle_command(
        "Jarvis status",
        config,
        {"paired": True, "link": "connected", "name": "Ray-Ban Meta", "battery": 82, "worn": True},
    )
    assert answer == "Ray-Ban Meta link is connected, battery 82 percent, currently worn. Jarvis PC is online."


def test_glasses_status_handles_unpaired():
    assert glasses_status({"paired": False, "link": "disconnected"}) == "The glasses are not paired."


def test_create_note_writes_only_inside_approved_root(tmp_path: Path):
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    assert create_note("Hey Meta ask Jarvis take a note buy air filters", config) == "Saved that to today's Jarvis notes."
    note = next((tmp_path / "Jarvis Notes").glob("*.md"))
    assert "buy air filters" in note.read_text(encoding="utf-8")


def test_close_named_target_kills_chrome_when_running(monkeypatch):
    killed: list[list[str]] = []
    states = {"chrome.exe": True}

    def process_running(name: str) -> bool:
        return bool(states.get(name.lower(), False))

    def taskkill(*args, **kwargs):
        cmd = list(args[0])
        killed.append(cmd)
        if "/IM" in cmd:
            states[cmd[cmd.index("/IM") + 1].lower()] = False
        return type("R", (), {"stdout": "", "returncode": 0})()

    monkeypatch.setattr(jarvis_module, "_process_running", process_running)
    monkeypatch.setattr(jarvis_module, "_visible_window_titles", lambda: ("YouTube - Google Chrome",))
    monkeypatch.setattr(jarvis_module, "_close_matching_windows", lambda pattern: 1)
    monkeypatch.setattr(jarvis_module.subprocess, "run", taskkill)
    assert close_named_target("Hey Jarvis close chrome") == "Confirmed Chrome is closed."
    assert any(cmd[:2] == ["taskkill.exe", "/IM"] for cmd in killed)
    assert classify_command("hey jarvis close chrome") == "close"
    assert plan_commands("Jarvis open chrome then close chrome", "jarvis") == ["open chrome", "close chrome"]


def test_close_named_target_reports_not_open(monkeypatch):
    monkeypatch.setattr(jarvis_module, "_process_running", lambda name: False)
    monkeypatch.setattr(jarvis_module, "_visible_window_titles", lambda: ())
    assert close_named_target("Jarvis close chrome") == "Chrome is not open."


def test_handle_command_routes_close_chrome_natively(monkeypatch, tmp_path: Path):
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    calls: list[str] = []
    monkeypatch.setattr(
        jarvis_module,
        "close_named_target",
        lambda text: calls.append(text) or "Confirmed Chrome is closed.",
    )
    monkeypatch.setattr(
        jarvis_module,
        "start_operator_task",
        lambda text, config: (_ for _ in ()).throw(AssertionError("close must not use operator")),
    )
    assert handle_command("Hey Jarvis close chrome", config) == "Confirmed Chrome is closed."
    assert calls == ["close chrome"]


def test_open_named_target_uses_allowlisted_dashboard(monkeypatch, tmp_path: Path):
    opened = []
    monkeypatch.setattr("cloudos.glasses.jarvis._open_target", opened.append)
    monkeypatch.setattr("cloudos.glasses.jarvis._http_ok", lambda url: True)
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    assert open_named_target("Jarvis open dashboard", config) == "Confirmed the Cloud AI dashboard is online and opened it."
    assert opened == ["http://127.0.0.1:8080/dashboard"]


def test_real_voice_spellings_open_n8n_before_dashboard(monkeypatch, tmp_path: Path):
    opened = []
    monkeypatch.setattr("cloudos.glasses.jarvis._open_target", opened.append)
    monkeypatch.setattr("cloudos.glasses.jarvis._http_ok", lambda url: True)
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    assert normalize_voice_text("ok meta ask jarvis to open n eight n") == "hey meta ask jarvis to open n8n"
    assert normalize_voice_text("hey meta ask jarvis to open n eightn") == "hey meta ask jarvis to open n8n"
    assert open_named_target("hey meta ask jarvis to open n eight n dashboard", config) == "Confirmed n8n is healthy and opened it."
    assert open_named_target("hey meta ask jarvis to open n a n", config) == "Confirmed n8n is healthy and opened it."
    assert opened == ["http://127.0.0.1:5679", "http://127.0.0.1:5679"]


def test_n8n_open_reports_live_health_failure_truthfully(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("cloudos.glasses.jarvis._open_target", lambda target: None)
    monkeypatch.setattr("cloudos.glasses.jarvis._http_ok", lambda url: False)
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    assert open_named_target("Jarvis open n8n", config) == "Opened n8n, but its health check did not answer."


def test_native_calculator_open_requires_process_verification(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("cloudos.glasses.jarvis._open_target", lambda target: None)
    monkeypatch.setattr("cloudos.glasses.jarvis._process_running", lambda name: name == "CalculatorApp.exe")
    monkeypatch.setattr("cloudos.glasses.jarvis.time.sleep", lambda seconds: None)
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    assert open_named_target("Jarvis open calculator", config) == "Confirmed Calculator is running."


def test_arbitrary_installed_app_uses_windows_start_catalog(monkeypatch, tmp_path: Path):
    launched = []
    monkeypatch.setattr(
        jarvis_module,
        "_windows_start_apps",
        lambda: (
            ("Paint", "Microsoft.Paint_8wekyb3d8bbwe!App"),
            ("Spotify", "Spotify.exe"),
        ),
    )
    monkeypatch.setattr(jarvis_module, "_open_start_app", launched.append)
    monkeypatch.setattr(jarvis_module, "_window_visible", lambda name: name == "Paint")
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    assert open_named_target("Jarvis open Paint", config) == "Confirmed Paint is visible."
    assert launched == ["Microsoft.Paint_8wekyb3d8bbwe!App"]


@pytest.mark.parametrize("spoken", ["grockbot", "rock bot", "grok bot", "grokbot"])
def test_voice_misspellings_still_resolve_grok_bot(monkeypatch, spoken: str):
    monkeypatch.setattr(
        jarvis_module,
        "_windows_start_apps",
        lambda: (("Grok Bot", "electron.app.Grok Bot"),),
    )
    assert jarvis_module._resolve_start_app(spoken) == ("Grok Bot", "electron.app.Grok Bot")


def test_grok_bot_opens_direct_exe_for_voice_aliases(monkeypatch, tmp_path: Path):
    local = tmp_path / "AppData" / "Local"
    installed = local / "Programs" / "Grok Bot"
    installed.mkdir(parents=True)
    exe = installed / "Grok Bot.exe"
    exe.write_bytes(b"mz")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    opened: list[object] = []
    monkeypatch.setattr(jarvis_module, "_open_target", opened.append)
    monkeypatch.setattr(jarvis_module, "_process_running", lambda name: name == "Grok Bot.exe")
    monkeypatch.setattr(jarvis_module, "_window_visible", lambda name: False)
    monkeypatch.setattr(jarvis_module.time, "sleep", lambda seconds: None)
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    for spoken in ("open grockbot", "open rock bot", "open grokbot", "open grok"):
        opened.clear()
        reply = open_named_target(f"Jarvis {spoken}", config)
        assert reply == "Confirmed Grok Bot is running."
        assert opened == [exe]


def test_open_rock_bot_does_not_open_random_markdown(monkeypatch, tmp_path: Path):
    (tmp_path / "oTQPxPFROck.md").write_text("nope", encoding="utf-8")
    local = tmp_path / "empty-local"
    local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    opened: list[object] = []
    monkeypatch.setattr(jarvis_module, "_open_target", opened.append)
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    reply = open_named_target("Jarvis open rock bot", config)
    assert "oTQPxPFROck.md" not in reply
    assert opened == []
    assert "not installed" in reply.lower()


def test_bare_open_asks_for_app_then_followup_opens_grok(monkeypatch, tmp_path: Path):
    local = tmp_path / "AppData" / "Local"
    installed = local / "Programs" / "Grok Bot"
    installed.mkdir(parents=True)
    (installed / "Grok Bot.exe").write_bytes(b"mz")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(jarvis_module, "_open_target", lambda target: None)
    monkeypatch.setattr(jarvis_module, "_process_running", lambda name: name == "Grok Bot.exe")
    monkeypatch.setattr(jarvis_module, "_window_visible", lambda name: False)
    monkeypatch.setattr(jarvis_module.time, "sleep", lambda seconds: None)
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    session = {"active": True, "last_actions": []}
    first = handle_command("Hey Jarvis open", config, session=session)
    assert "Which app" in first
    second = handle_command("grokbot", config, session=session)
    assert "Grok Bot" in second


def test_grok_ai_command_routing_and_payload():
    assert classify_command("Jarvis tell grokbot about the new information") == "grok_ai"
    assert classify_command("Jarvis ai do summarize my scholarships") == "grok_ai"
    assert classify_command("Jarvis read my new information") == "grok_ai"
    assert classify_command("Jarvis what did grok say") == "grok_ai"
    assert classify_command("Jarvis read grok result") == "grok_ai"
    payload, read_only = jarvis_module._extract_grok_payload(
        "Jarvis tell grok bot about the deploy status"
    )
    assert payload == "the deploy status"
    assert read_only is False
    payload2, read_only2 = jarvis_module._extract_grok_payload("Jarvis read my new information")
    assert "new information" in payload2.lower()
    assert read_only2 is False
    _, read_only3 = jarvis_module._extract_grok_payload("Jarvis what did grok say")
    assert read_only3 is True


def test_control_grok_bot_types_prompt_and_reads_reply(monkeypatch, tmp_path: Path):
    class FakePrompt:
        def set_focus(self):
            return None

        def click_input(self):
            return None

    class FakeWindow:
        def set_focus(self):
            return None

    sent: list[str] = []
    state = {"t": 0.0, "polls": 0}

    def fake_texts(_window):
        state["polls"] += 1
        if state["polls"] <= 1:
            return ["old bubble"]
        return ["old bubble", "Fresh Grok reply about scholarships"]

    monkeypatch.setattr(jarvis_module, "ensure_grok_bot_ready", lambda config: None)
    monkeypatch.setattr(jarvis_module, "_find_grok_bot_window", lambda: FakeWindow())
    monkeypatch.setattr(jarvis_module, "_grok_prompt_edit", lambda window: FakePrompt())
    monkeypatch.setattr(jarvis_module, "_grok_visible_message_texts", fake_texts)
    monkeypatch.setattr(jarvis_module.time, "sleep", lambda seconds: state.__setitem__("t", state["t"] + max(float(seconds), 0.1)))
    monkeypatch.setattr(jarvis_module.time, "monotonic", lambda: state["t"])

    class FakeKeyboard:
        @staticmethod
        def send_keys(keys, with_spaces=False, pause=0.01):
            sent.append(f"keys:{keys}")

    import sys
    from types import ModuleType

    keyboard = ModuleType("pywinauto.keyboard")
    keyboard.send_keys = FakeKeyboard.send_keys
    monkeypatch.setitem(sys.modules, "pywinauto.keyboard", keyboard)

    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    reply = jarvis_module.control_grok_bot("Jarvis tell grok bot about scholarships", config)
    assert any(item.startswith("keys:") and "scholarships" in item for item in sent)
    assert "Fresh Grok reply about scholarships" in reply


def test_common_windows_app_aliases_resolve_start_catalog(monkeypatch):
    monkeypatch.setattr(
        jarvis_module,
        "_windows_start_apps",
        lambda: (
            ("Microsoft Store", "Microsoft.WindowsStore_8wekyb3d8bbwe!App"),
            ("Microsoft Edge", "MSEdge"),
        ),
    )
    assert jarvis_module._resolve_start_app("the app store") == (
        "Microsoft Store",
        "Microsoft.WindowsStore_8wekyb3d8bbwe!App",
    )
    assert jarvis_module._resolve_start_app("edge") == ("Microsoft Edge", "MSEdge")


def test_arbitrary_app_visibility_uses_real_window_titles(monkeypatch):
    monkeypatch.setattr(
        jarvis_module,
        "_visible_window_titles",
        lambda: ("Untitled - Paint", "Calculator"),
    )
    assert jarvis_module._window_visible("Paint") is True
    assert jarvis_module._window_visible("Grok Bot") is False


@pytest.mark.parametrize(
    ("command", "uri"),
    [
        ("Jarvis open my settings", "ms-settings:"),
        ("Jarvis open Bluetooth settings", "ms-settings:bluetooth"),
        ("Jarvis open display settings", "ms-settings:display"),
        ("Jarvis open Windows Update settings", "ms-settings:windowsupdate"),
    ],
)
def test_windows_settings_pages_open_natively(monkeypatch, tmp_path: Path, command: str, uri: str):
    opened = []
    monkeypatch.setattr(jarvis_module, "_open_target", opened.append)
    monkeypatch.setattr(jarvis_module, "_process_running", lambda name: name == "SystemSettings.exe")
    monkeypatch.setattr(jarvis_module.time, "sleep", lambda seconds: None)
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    assert open_named_target(command, config) == "Confirmed Windows Settings is running."
    assert opened == [uri]


def test_general_safe_multistep_plan_supports_arbitrary_apps_and_settings():
    assert plan_commands(
        "Hey Jarvis open Paint then open my settings and then open Calculator",
        "jarvis",
    ) == ["open paint", "open my settings", "open calculator"]
    assert plan_commands(
        "Hey Jarvis launch Chrome then Notepad then Paint",
        "jarvis",
    ) == ["launch chrome", "launch notepad", "launch paint"]


def test_notepad_document_workflow_is_classified_natively():
    command = (
        "Open Notepad, type GLASSES-JARVIS-TEST, save it to Desktop as "
        "jarvis-glasses-test.txt and tell me when it is saved."
    )
    assert classify_command(command) == "notepad_document"
    assert jarvis_module.parse_notepad_document_workflow(command) == {
        "text": "GLASSES-JARVIS-TEST",
        "filename": "jarvis-glasses-test.txt",
        "folder": "Desktop",
    }


def test_notepad_document_workflow_writes_verified_desktop_file(monkeypatch, tmp_path: Path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(jarvis_module.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(jarvis_module, "_open_target", lambda target: None)
    monkeypatch.setattr(jarvis_module, "_process_running", lambda name: name == "Notepad.exe")
    monkeypatch.setattr(jarvis_module, "_window_visible", lambda name: True)
    monkeypatch.setattr(jarvis_module, "_type_into_notepad", lambda text: None)
    monkeypatch.setattr(jarvis_module, "_save_notepad_document", lambda path: None)
    monkeypatch.setattr(jarvis_module.time, "sleep", lambda seconds: None)

    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    answer = jarvis_module.notepad_document_workflow(
        "Jarvis open Notepad, type GLASSES-JARVIS-TEST, save it to Desktop as jarvis-glasses-test.txt",
        config,
    )
    path = desktop / "jarvis-glasses-test.txt"
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "GLASSES-JARVIS-TEST"
    assert answer == f"Confirmed Notepad saved GLASSES-JARVIS-TEST to {path}."


def test_claim_command_deduplicates_request_and_transcript_with_correlation_ids(tmp_path: Path):
    async def scenario():
        config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
        session = bridge.new_session()
        first = bridge.claim_command(session, "Hey Jarvis open Calculator", config, "voice.requests", "voice-1")
        duplicate = bridge.claim_command(session, "open calculator", config, "voice.transcripts", "transcript-1")
        assert first["accepted"] is True
        assert duplicate["accepted"] is False
        assert duplicate["duplicate_of"] == first["correlation_id"]
        assert first["correlation_id"] != duplicate["correlation_id"]

    asyncio.run(scenario())


def test_scheduled_task_is_retained_and_unexpected_exception_is_logged(monkeypatch, tmp_path: Path, capsys):
    async def scenario():
        async def fail(*args, **kwargs):
            raise RuntimeError("background boom")

        monkeypatch.setattr("scripts.jarvis_glasses.finish_computer_task", fail)
        config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
        session = bridge.new_session()
        task = bridge.schedule_computer_task(object(), config, session, "Jarvis inspect the screen", "corr-1")
        assert task in session["tasks"]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not session["tasks"]

    asyncio.run(scenario())
    captured = capsys.readouterr()
    assert "background_task_failed" in captured.err
    assert "background boom" in captured.err


def test_assistant_tool_can_finish_and_speak_after_tool_turn_closes(monkeypatch, tmp_path: Path):
    class FakeVisor:
        def __init__(self):
            self.calls = []

        async def subscribe(self, topic, options):
            yield {"id": "tool-1", "arguments": {"command": "inspect the visible desktop"}}

        async def call(self, method, params=None):
            self.calls.append((method, params))
            return {}

    async def scenario():
        async def delayed_answer(visor, config, text, session=None):
            await asyncio.sleep(0.01)
            return "Verified the visible desktop."

        monkeypatch.setattr("scripts.jarvis_glasses.answer_text", delayed_answer)
        visor = FakeVisor()
        config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
        session = bridge.new_session()
        await serve_assistant_tools(visor, config, session)
        assert [method for method, _ in visor.calls] == ["assistant.tools.answer"]
        await asyncio.gather(*tuple(session["tasks"]))
        assert [method for method, _ in visor.calls] == ["assistant.tools.answer", "voice.speak"]
        assert visor.calls[-1][1]["text"] == "Verified the visible desktop."

    asyncio.run(scenario())


def test_named_reply_keeps_voice_request_conversation_open(tmp_path: Path):
    class FakeVisor:
        def __init__(self):
            self.calls = []

        async def subscribe(self, topic, options):
            yield {"id": "voice-1", "text": "Hey Jarvis"}

        async def call(self, method, params=None):
            self.calls.append((method, params))
            return {"paired": True, "link": "connected"}

    async def scenario():
        config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
        session = bridge.new_session()
        visor = FakeVisor()
        await bridge.serve_named_requests(visor, config, session)
        assert visor.calls == [
            ("voice.reply", {"request": "voice-1", "text": "Yes?", "listen": True})
        ]
        assert session["active"] is True
        assert session.get("conversation_id")

    asyncio.run(scenario())


def test_private_transcript_stream_reopens_when_rearmed(tmp_path: Path):
    class WaitingStream:
        def __init__(self):
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    class FakeVisor:
        def __init__(self):
            self.streams = []

        async def open(self, topic, options):
            stream = WaitingStream()
            self.streams.append(stream)
            return stream

    async def scenario():
        visor = FakeVisor()
        config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
        session = bridge.new_session()
        listener = asyncio.create_task(bridge.serve_private_hotword(visor, config, session))
        for _ in range(20):
            if listener.done():
                await listener
            if visor.streams:
                break
            await asyncio.sleep(0.01)
        assert visor.streams
        session["restart_transcripts"].set()
        for _ in range(20):
            if listener.done():
                await listener
            if len(visor.streams) >= 2:
                break
            await asyncio.sleep(0)
        listener.cancel()
        with pytest.raises(asyncio.CancelledError):
            await listener
        assert len(visor.streams) >= 2
        assert visor.streams[0].closed is True

    asyncio.run(scenario())


def test_bridge_retries_an_unexpected_worker_failure(monkeypatch, tmp_path: Path, capsys):
    calls = 0

    async def fake_serve_once(config, token):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("subscription crashed")
        raise SystemExit

    async def no_delay(seconds):
        return None

    async def scenario():
        monkeypatch.setattr(bridge, "serve_once", fake_serve_once)
        monkeypatch.setattr(bridge.asyncio, "sleep", no_delay)
        config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
        with pytest.raises(SystemExit):
            await bridge.serve_forever(config, "token")

    asyncio.run(scenario())
    assert calls == 2
    assert "Unexpected bridge cycle failure" in capsys.readouterr().err


def test_generic_tasks_use_a_fifteen_minute_operator_timeout(monkeypatch, tmp_path: Path):
    observed = {}

    def fake_run_codex(task, workspace, timeout_s):
        observed["timeout_s"] = timeout_s
        return {"output": "Verified completion."}

    class FakeVisor:
        async def call(self, method, params=None):
            return {"paired": True}

    async def scenario():
        monkeypatch.setattr("scripts.jarvis_glasses._run_codex", fake_run_codex)
        from scripts.jarvis_glasses import answer_text

        config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
        assert await answer_text(FakeVisor(), config, "Jarvis inspect the visible desktop") == "Verified completion."

    asyncio.run(scenario())
    assert bridge.GENERIC_TASK_TIMEOUT_S == 900
    assert observed["timeout_s"] == bridge.GENERIC_TASK_TIMEOUT_S


def test_operator_timeout_message_uses_configured_minutes(monkeypatch, tmp_path: Path):
    def fake_run_codex(task, workspace, timeout_s):
        raise TimeoutError(f"Codex task timed out after {timeout_s}s")

    class FakeVisor:
        async def call(self, method, params=None):
            return {"paired": True}

    async def scenario():
        monkeypatch.setattr("scripts.jarvis_glasses._run_codex", fake_run_codex)
        from scripts.jarvis_glasses import answer_text

        config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
        reply = await answer_text(FakeVisor(), config, "Jarvis fix the outreach sender")
        assert "15-minute limit" in reply
        assert "two minutes" not in reply

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "task",
    [
        "please send a message to Sam",
        "go ahead and purchase the item",
        "change my account settings",
        "weaken Windows security",
    ],
)
def test_codex_operator_blocks_high_risk_requests_before_launch(task: str, tmp_path: Path):
    with pytest.raises(CloudOSError):
        _run_codex(task, tmp_path, 120)


def test_open_google_chrome_is_an_app_command_not_an_empty_search():
    assert classify_command("hey meta ask jarvis to open google chrome") == "open"


def test_real_voice_web_search_keeps_query(monkeypatch):
    opened = []
    monkeypatch.setattr("cloudos.glasses.jarvis._open_target", opened.append)
    answer = search_web("hey meta ask jarvis to search the web for lsu construction management", "jarvis")
    assert answer == "Opened a web search for lsu construction management."
    assert opened == ["https://www.google.com/search?q=lsu+construction+management"]


def test_multi_step_plan_separates_browser_actions_from_query_tail():
    assert plan_commands(
        "hey chat jarvis open chrome search google for lsu construction management and tell me when the search is complete",
        "jarvis",
    ) == ["open chrome", "search google for lsu construction management and tell me when the search is complete"]
    assert plan_commands(
        "hey meta ask jarvis to open chrome and then open n eightn",
        "jarvis",
    ) == ["open chrome", "open n8n"]
    assert plan_commands(
        "hey meta ask jarvis to open chrome and then docker",
        "jarvis",
    ) == ["open chrome", "open docker"]


def test_multi_step_command_opens_chrome_then_docker(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr(
        "cloudos.glasses.jarvis.open_named_target",
        lambda text, config: calls.append(text) or f"finished {text}",
    )
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    answer = handle_command("Jarvis open Chrome then Docker", config)
    assert calls == ["open chrome", "open docker"]
    assert answer == "finished open chrome finished open docker"


def test_unknown_request_queues_existing_codex_job(monkeypatch, tmp_path: Path):
    saved = {}
    monkeypatch.setattr("cloudos.api.store.create_job", lambda kind, payload, max_attempts: saved.update(kind=kind, payload=payload, max_attempts=max_attempts) or {"id": "job-1"})
    monkeypatch.setattr("cloudos.glasses.jarvis._save_operator_state", lambda value: saved.update(state=value))
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    answer = start_operator_task("Hey Meta ask Jarvis to inspect the project tests", config)
    assert answer == "I started that on the mini PC. Ask for task status."
    assert saved["kind"] == "task.run"
    assert saved["payload"]["provider"] == "codex"
    assert saved["payload"]["task"] == "inspect the project tests"
    assert saved["max_attempts"] == 1


def test_operator_status_reads_verified_job_result(monkeypatch):
    monkeypatch.setattr("cloudos.glasses.jarvis._load_operator_state", lambda: {"job_id": "job-1"})
    monkeypatch.setattr("cloudos.api.store.get_job", lambda job_id: {"status": "succeeded", "result": {"output": "All checks passed."}})
    assert operator_task_status() == "Finished. All checks passed."


def test_multi_step_command_executes_in_order(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr(
        "cloudos.glasses.jarvis.open_named_target",
        lambda text, config: calls.append(("open", text)) or f"finished {text}",
    )
    monkeypatch.setattr(
        "cloudos.glasses.jarvis.search_web",
        lambda text, phrase: calls.append(("search", text)) or "search finished",
    )
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    answer = handle_command("Jarvis open Chrome then open n8n then search the web for LSU CM", config)
    assert calls == [("open", "open chrome"), ("open", "open n8n"), ("search", "search the web for lsu cm")]
    assert answer == "finished open chrome finished open n8n search finished"


def test_real_vdk_client_voice_request_and_reply(monkeypatch, tmp_path: Path):
    """Exercise the installed visor-kit client over its real WebSocket/JSON-RPC path."""
    replies = []

    async def scenario():
        async def handler(socket):
            subscription = None
            async for raw in socket:
                message = json.loads(raw)
                method = message["method"]
                if method == "kit.hello":
                    result = {
                        "protocol": 1,
                        "methods": ["glasses.status", "voice.reply"],
                        "topics": ["voice.requests"],
                        "feeds": [],
                        "scopes": ["state", "voice"],
                    }
                elif method == "glasses.status":
                    result = {"paired": True, "link": "connected", "name": "Test glasses", "battery": 90}
                elif method == "kit.subscribe":
                    subscription = message["id"]
                    result = {}
                elif method == "voice.reply":
                    replies.append(message["params"])
                    result = {}
                else:
                    raise AssertionError(f"Unexpected method: {method}")
                await socket.send(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}))
                if method == "kit.subscribe":
                    await socket.send(json.dumps({
                        "jsonrpc": "2.0",
                        "method": "kit.event",
                        "params": {
                            "subscription": subscription,
                            "event": {"id": "voice-1", "text": "Jarvis status", "phrase": "jarvis"},
                        },
                    }))
                if method == "voice.reply":
                    await socket.send(json.dumps({
                        "jsonrpc": "2.0",
                        "method": "kit.ended",
                        "params": {"subscription": subscription},
                    }))

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            config = BridgeConfig(f"ws://127.0.0.1:{port}/rpc", (tmp_path,))
            await serve_once(config, "test-token")

    monkeypatch.setattr("cloudos.glasses.jarvis.pc_status", lambda: "Jarvis PC is online.")
    asyncio.run(scenario())
    assert replies == [{
        "request": "voice-1",
        "text": "Test glasses link is connected, battery 90 percent. Jarvis PC is online.",
        "listen": True,
    }]


def test_named_subscription_uses_concrete_jarvis_phrase(tmp_path: Path):
    class FakeVisor:
        def __init__(self):
            self.options = None

        async def subscribe(self, topic, options):
            self.options = options
            if False:
                yield {}

    async def scenario():
        visor = FakeVisor()
        config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
        await bridge.serve_named_requests(visor, config, bridge.new_session())
        assert visor.options == {
            "phrases": ["Hey Jarvis", "Jarvis", "hey jarvis", "jarvis"],
        }

    asyncio.run(scenario())

def test_click_that_asks_when_ambiguous(monkeypatch):
    monkeypatch.setattr(
        jarvis_module,
        "_visible_uia_candidates",
        lambda limit=40: [
            {"name": "Document1 - Notepad", "control_type": "Window", "handle": 1},
            {"name": "Calculator", "control_type": "Window", "handle": 2},
        ],
    )
    answer = jarvis_module.click_that_target("Hey Jarvis click that")
    assert "Which visible target" in answer
    assert "Calculator" in answer


def test_click_that_clicks_unique_match(monkeypatch):
    monkeypatch.setattr(
        jarvis_module,
        "_visible_uia_candidates",
        lambda limit=40: [
            {"name": "Calculator", "control_type": "Window", "handle": 2},
        ],
    )

    class FakeWindow:
        handle = 2

        def set_focus(self):
            return None

        def click_input(self):
            return None

    class FakeDesktop:
        def __init__(self, backend="uia"):
            pass

        def windows(self):
            return [FakeWindow()]

    import types
    import sys
    fake = types.ModuleType("pywinauto")
    fake.Desktop = FakeDesktop
    monkeypatch.setitem(sys.modules, "pywinauto", fake)
    answer = jarvis_module.click_that_target("Hey Jarvis click Calculator")
    assert answer == "Clicked Calculator."


def test_capability1_multistep_single_utterance_runs_three_plus_actions(monkeypatch, tmp_path: Path):
    """Capability 1: one utterance must execute every planned step before reporting done."""
    calls: list[tuple[str, str]] = []

    def fake_open(target):
        calls.append(("open_target", str(target)))

    monkeypatch.setattr(jarvis_module, "_open_target", fake_open)
    monkeypatch.setattr(jarvis_module, "_process_running", lambda name: True)
    monkeypatch.setattr(jarvis_module, "_window_visible", lambda name: True)
    monkeypatch.setattr(jarvis_module, "_visible_window_titles", lambda: ("LSU - Google Search - Google Chrome",))
    monkeypatch.setattr(jarvis_module, "browser_followthrough", lambda text: calls.append(("follow", text)) or f"done:{text}")
    monkeypatch.setattr(jarvis_module.time, "sleep", lambda seconds: None)
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    session = {"conversation_id": "conv-1", "last_actions": [], "active": True}
    answer = handle_command(
        "Hey Jarvis open Chrome, search LSU construction management, open the relevant page, and summarize it.",
        config,
        session=session,
    )
    assert len(session["last_actions"]) >= 3
    assert [item["classification"] for item in session["last_actions"][:4]] == [
        "open",
        "web",
        "browser_followthrough",
        "browser_followthrough",
    ]
    assert any("chrome" in str(target).lower() or "google.com/search" in str(target).lower() for _, target in calls if True)
    assert "Confirmed Chrome" in answer or "web search" in answer.lower() or "LSU" in answer
    # Must not stop after the first tool-only confirmation.
    assert len(session["last_actions"]) > 1


def test_capability2_multiturn_reuses_same_conversation_and_browser_context(monkeypatch, tmp_path: Path):
    """Capability 2: chained turns keep conversation_id and reuse prior tool context."""
    opened: list[str] = []
    monkeypatch.setattr(jarvis_module, "_open_target", lambda target: opened.append(str(target)))
    monkeypatch.setattr(jarvis_module, "_process_running", lambda name: True)
    monkeypatch.setattr(jarvis_module, "_window_visible", lambda name: True)
    monkeypatch.setattr(jarvis_module.time, "sleep", lambda seconds: None)
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    session = {"conversation_id": "conv-keep", "last_actions": [], "active": True}
    first = handle_command("Hey Jarvis open Chrome", config, session=session)
    assert "Chrome" in first
    assert session["conversation_id"] == "conv-keep"
    second = handle_command("now search LSU construction management", config, session=session)
    assert session["conversation_id"] == "conv-keep"
    assert any("google.com/search" in item for item in opened)
    assert session["last_actions"][-1]["classification"] == "web"
    assert "lsu construction management" in second.lower()


def test_capability3_listen_flag_follows_session_active_not_hardcoded_false():
    """Capability 3: hands-free listen is a session transition flag, not a permanent off switch."""
    source = Path("scripts/jarvis_glasses.py").read_text(encoding="utf-8")
    assert '"listen": bool(listen)' in source or '"listen": bool(listen)' in source.replace(" ", "")
    assert 'listen = bool(session.get("active", False))' in source
    assert "unsupported wildcard follow-up routing is disabled" not in source


def test_capability2_named_requests_preserve_conversation_id_across_turns(monkeypatch, tmp_path: Path):
    """Simulated Visor multi-turn: same conversation_id, listen stays on, context reused."""
    class FakeVisor:
        def __init__(self):
            self.calls = []
            self._requests = iter(
                [
                    {"id": "r1", "text": "Hey Jarvis open Chrome"},
                    {"id": "r2", "text": "Hey Jarvis now search LSU construction management"},
                ]
            )

        async def subscribe(self, topic, options):
            for item in self._requests:
                yield item

        async def call(self, method, params=None):
            self.calls.append((method, params))
            if method == "glasses.status":
                return {"paired": True, "link": "connected"}
            return {}

    async def scenario():
        opened = []
        monkeypatch.setattr(jarvis_module, "_open_target", lambda target: opened.append(str(target)))
        monkeypatch.setattr(jarvis_module, "_process_running", lambda name: True)
        monkeypatch.setattr(jarvis_module, "_window_visible", lambda name: True)
        monkeypatch.setattr(jarvis_module.time, "sleep", lambda seconds: None)
        config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
        session = bridge.new_session()
        conv = session["conversation_id"]
        await bridge.serve_named_requests(FakeVisor(), config, session)
        assert session["conversation_id"] == conv
        assert session["active"] is True
        assert len(session["last_actions"]) >= 2
        assert session["last_actions"][0]["classification"] == "open"
        assert session["last_actions"][1]["classification"] == "web"
        replies = [params for method, params in FakeVisor().calls if False]
        # Recreate from a fresh run capture via monkeypatch side channel
        assert any("google.com/search" in item for item in opened)

    # Need to capture reply listen flags from the same FakeVisor instance
    async def scenario2():
        opened = []
        monkeypatch.setattr(jarvis_module, "_open_target", lambda target: opened.append(str(target)))
        monkeypatch.setattr(jarvis_module, "_process_running", lambda name: True)
        monkeypatch.setattr(jarvis_module, "_window_visible", lambda name: True)
        monkeypatch.setattr(jarvis_module.time, "sleep", lambda seconds: None)
        config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
        session = bridge.new_session()
        conv = session["conversation_id"]
        visor = FakeVisor()
        await bridge.serve_named_requests(visor, config, session)
        assert session["conversation_id"] == conv
        assert len([c for c in visor.calls if c[0] == "voice.reply"]) == 2
        assert all(c[1]["listen"] is True for c in visor.calls if c[0] == "voice.reply")
        assert any("google.com/search" in item for item in opened)
        assert len(session["last_actions"]) >= 2

    asyncio.run(scenario2())


def test_n8n_status_check_does_not_route_to_generic_pc_status(monkeypatch, tmp_path: Path):
    opened = []
    monkeypatch.setattr(jarvis_module, "_open_target", lambda target: opened.append(str(target)))
    monkeypatch.setattr(jarvis_module, "_http_ok", lambda url: True)
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    assert classify_command("check status on n8n work clothes") == "n8n_status"
    answer = handle_command("Hey Jarvis check status on n8n work clothes", config)
    assert "n8n is healthy" in answer.lower()
    assert any("5679" in item for item in opened)


def test_play_media_opens_watch_url_not_just_search(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(jarvis_module, "_open_target", opened.append)
    monkeypatch.setattr(
        jarvis_module,
        "_resolve_youtube_watch_url",
        lambda query, timeout=10.0: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    assert classify_command("play purple rain on youtube") == "play_media"
    reply = play_media_query("play purple rain on youtube")
    assert reply == "Playing purple rain on YouTube."
    assert opened == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]


def test_play_media_falls_back_to_search_when_watch_unresolved(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(jarvis_module, "_open_target", opened.append)
    monkeypatch.setattr(jarvis_module, "_resolve_youtube_watch_url", lambda query, timeout=10.0: None)
    reply = play_media_query("play purple rain on youtube")
    assert "could not start a specific video" in reply
    assert opened and "youtube.com/results" in opened[0] and "purple+rain" in opened[0]


def test_type_into_focus_sends_keys(monkeypatch):
    sent: list[str] = []

    class FakeWindow:
        def is_visible(self):
            return True

        def set_focus(self):
            return None

    class FakeDesktop:
        def __init__(self, backend="uia"):
            pass

        def windows(self):
            return [FakeWindow()]

    import sys
    import types

    fake = types.ModuleType("pywinauto")
    fake.Desktop = FakeDesktop
    keyboard = types.ModuleType("pywinauto.keyboard")
    keyboard.send_keys = lambda text, with_spaces=True, pause=0.02: sent.append(text)
    fake.keyboard = keyboard
    monkeypatch.setitem(sys.modules, "pywinauto", fake)
    monkeypatch.setitem(sys.modules, "pywinauto.keyboard", keyboard)
    monkeypatch.setattr(jarvis_module.time, "sleep", lambda seconds: None)
    reply = jarvis_module.type_into_focus("Jarvis type hello world")
    assert reply.startswith("Typed")
    assert sent == ["hello world"]


def test_click_hint_strips_play_verify_noise():
    hint = jarvis_module._click_hint(
        "click the relevant song result to start playback. verify it is playing."
    )
    assert "verify" not in hint
    assert "press play" not in hint


def test_send_requires_confirmation_then_executes(monkeypatch, tmp_path: Path):
    jarvis_module._clear_pending_risk()
    monkeypatch.setattr(jarvis_module, "click_that_target", lambda text: f"clicked:{text}")
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    staged = handle_command("Hey Jarvis click send", config)
    assert "confirm send" in staged.lower()
    done = handle_command("Hey Jarvis confirm send", config)
    assert done.startswith("clicked:")


def test_edit_document_append_and_backup(tmp_path: Path):
    docs = tmp_path / "Documents"
    docs.mkdir()
    target = docs / "jarvis-edit-demo.txt"
    target.write_text("hello\n", encoding="utf-8")
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (docs,))
    reply = jarvis_module.edit_document("append world to jarvis-edit-demo.txt", config)
    assert "Appended" in reply
    assert "world" in target.read_text(encoding="utf-8")
    assert (docs / "jarvis-edit-demo.txt.bak").is_file()


def test_list_and_read_documents(tmp_path: Path):
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "alpha-notes.txt").write_text("alpha body", encoding="utf-8")
    (docs / "beta-report.md").write_text("beta body", encoding="utf-8")
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (docs,))
    listed = handle_command("Hey Jarvis check my documents", config)
    assert "Recent documents:" in listed
    assert "alpha-notes.txt" in listed
    read = handle_command("Hey Jarvis read the file alpha-notes.txt", config)
    assert "alpha body" in read


def test_delete_file_requires_confirmation(tmp_path: Path):
    jarvis_module._clear_pending_risk()
    docs = tmp_path / "Documents"
    docs.mkdir()
    target = docs / "doomed.txt"
    target.write_text("bye\n", encoding="utf-8")
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (docs,))
    staged = handle_command("Hey Jarvis delete the file doomed.txt", config)
    assert "confirm delete" in staged.lower()
    assert target.is_file()
    done = handle_command("Hey Jarvis confirm delete", config)
    assert "Deleted doomed.txt" in done
    assert not target.exists()


def test_audit_manage_and_fix_routing(monkeypatch, tmp_path: Path):
    assert classify_command("Jarvis audit the system") == "system_audit"
    assert classify_command("Jarvis run an audit") == "system_audit"
    assert classify_command("Jarvis audit my documents") == "list_documents"
    assert classify_command("Jarvis manage docker") == "manage"
    assert classify_command("Jarvis fix n8n workflows") == "operator"
    assert classify_command("Jarvis repair the outreach sender") == "operator"
    monkeypatch.setattr(jarvis_module, "_http_json", lambda url, timeout=1.25: {"status": "ok"})
    monkeypatch.setattr(jarvis_module, "top_processes", lambda: "The biggest running processes are chrome, python, docker.")
    report = jarvis_module.system_audit_report()
    assert "API healthy" in report
    assert "n8n healthy" in report
    opened = []
    monkeypatch.setattr(jarvis_module, "open_named_target", lambda text, config: opened.append(text) or f"opened {text}")
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    manage = jarvis_module.manage_named_target("Jarvis manage docker", config)
    assert opened == ["open docker"]
    assert "opened" in manage


def test_operator_refuses_high_risk_at_voice_edge(monkeypatch, tmp_path: Path):
    created = []
    monkeypatch.setattr(
        "cloudos.api.store.create_job",
        lambda *args, **kwargs: created.append(True) or {"id": "job-x", "created_at": "now"},
    )
    config = BridgeConfig("ws://127.0.0.1:7333/rpc", (tmp_path,))
    reply = start_operator_task("Jarvis send a message to Sam", config)
    assert "confirmed workflow" in reply.lower() or "will not run" in reply.lower()
    assert created == []

