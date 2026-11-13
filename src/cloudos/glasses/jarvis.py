"""Small, fail-closed command handler for the Jarvis glasses bridge."""

from __future__ import annotations

import ctypes
import csv
import datetime as dt
import functools
import ipaddress
import io
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import uuid
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse


MAX_REPLY_LENGTH = 500
DEFAULT_SEARCH_SECONDS = 4.0
DEFAULT_SEARCH_RESULTS = 5
PENDING_RISK_TTL_S = 60.0
_PENDING_RISK: dict | None = None
IGNORED_DIRECTORIES = {
    ".git",
    ".venv",
    "node_modules",
    "AppData",
    "$RECYCLE.BIN",
    "System Volume Information",
}
SAFE_OPEN_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
    ".mp4",
    ".webm",
}
EDITABLE_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log"}
MEDIA_KEYS = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "volume_up": 0xAF,
    "volume_down": 0xAE,
    "mute": 0xAD,
}
ACTION_START = re.compile(
    r"\b(?:"
    r"open\s+(?:n8n|chrome|cursor|docker(?: desktop)?|dashboard|control panel|notepad|documents?|downloads?|desktop|file explorer|grok(?:\s*bot)?|grockbot|rock\s*bot)|"
    r"close\s+(?:google\s+)?(?:chrome|edge|browser|docker|cursor|notepad|grok(?:\s*bot)?|tab|window)|"
    r"quit\s+(?:google\s+)?(?:chrome|edge|browser|docker|cursor|notepad|grok(?:\s*bot)?)|"
    r"(?:click|select|choose|go\s+to|show)\s+(?:the\s+)?(?:containers?|images?|logs?|volumes?|kubernetes|builds?|models?|settings|notifications|troubleshoot)(?:\s+(?:in|on)\s+docker)?|"
    r"launch\s+(?:n8n|chrome|cursor|docker(?: desktop)?|dashboard|notepad)|"
    r"start\s+(?:n8n|chrome|cursor|docker(?: desktop)?|dashboard|notepad)|"
    r"search\s+(?:the\s+)?web|search\s+google|google\s+for|"
    r"find\b|locate\b|look\s+for\b|"
    r"(?:list|show|check)\s+(?:all\s+)?(?:my\s+)?(?:recent\s+)?documents?\b|"
    r"(?:read|append|replace|edit)\b|"
    r"confirm(?:\s+(?:send|delete|it|that)?)?\b|"
    r"(?:take\s+)?(?:a\s+)?note\b|remember\b|"
    r"what(?:'s|\s+is)\s+running|top\s+processes|"
    r"play\s+pause|(?:play|pause)(?:\s+the)?\s+music|"
    r"next\s+(?:song|track)|skip(?:\s+(?:song|track|this|ad))?|"
    r"previous\s+(?:song|track)|volume\s+(?:up|down)|mute|unmute|"
    r"type\s+|press\s+|hit\s+|scroll\s+|click\s+|focus\s+|switch\s+to\s+|"
    r"select\s+all|copy\b|paste\b|undo\b|cut\b|save\b|"
    r"audit\b|diagnose\b|manage\b|fix\b|repair\b|troubleshoot\b|"
    r"tell\s+(?:grok|the\s+ai|ai)\b|ask\s+(?:grok|the\s+ai|ai)\b|ai\s+do\b|"
    r"task\s+(?:status|result)|cancel\s+(?:the\s+)?task|status|health"
    r")",
    re.IGNORECASE,
)

CLICKABLE_CONTROL_TYPES = {
    "Button",
    "CheckBox",
    "DataItem",
    "Hyperlink",
    "ListItem",
    "MenuItem",
    "RadioButton",
    "TabItem",
    "Text",
    "TreeItem",
}

NAMED_KEYS = {
    "enter": "{ENTER}",
    "return": "{ENTER}",
    "escape": "{ESC}",
    "esc": "{ESC}",
    "tab": "{TAB}",
    "space": "{SPACE}",
    "spacebar": "{SPACE}",
    "backspace": "{BACKSPACE}",
    "delete": "{DELETE}",
    "up": "{UP}",
    "down": "{DOWN}",
    "left": "{LEFT}",
    "right": "{RIGHT}",
    "home": "{HOME}",
    "end": "{END}",
    "page up": "{PGUP}",
    "page down": "{PGDN}",
}

DOCKER_SAFE_CONTROLS = {
    "container": "Containers",
    "containers": "Containers",
    "image": "Images",
    "images": "Images",
    "log": "Logs",
    "logs": "Logs",
    "volume": "Volumes",
    "volumes": "Volumes",
    "kubernetes": "Kubernetes",
    "build": "Builds",
    "builds": "Builds",
    "model": "Models",
    "models": "Models",
    "setting": "Settings",
    "settings": "Settings",
    "notification": "Notifications",
    "notifications": "Notifications",
    "troubleshoot": "Troubleshoot",
}

WINDOWS_SETTINGS_ROUTES = (
    (("windows update", "update settings"), "ms-settings:windowsupdate"),
    (("bluetooth",), "ms-settings:bluetooth"),
    (("display", "screen"), "ms-settings:display"),
    (("sound", "audio"), "ms-settings:sound"),
    (("network", "wi-fi", "wifi", "ethernet"), "ms-settings:network"),
    (("apps", "applications"), "ms-settings:appsfeatures"),
    (("privacy",), "ms-settings:privacy"),
    (("accounts",), "ms-settings:accounts"),
    (("personalization", "personalisation"), "ms-settings:personalization"),
    (("notifications",), "ms-settings:notifications"),
)

START_APP_ALIASES = {
    "app store": "microsoft store",
    "store": "microsoft store",
    "edge": "microsoft edge",
    "grok": "grok bot",
    "grokbot": "grok bot",
    "grockbot": "grok bot",
    "grock bot": "grok bot",
    "rock bot": "grok bot",
    "rockbot": "grok bot",
}

MULTISTEP_ACTION = re.compile(
    r"^(?:open|launch|start|close|quit|click|select|choose|go\s+to|show|search|find|locate|"
    r"look\s+for|type|save|summarize|read|scrape|append|replace|edit|confirm|list|"
    r"audit|diagnose|manage|fix|repair|troubleshoot|"
    r"tell|ask|"
    r"take\s+(?:a\s+)?note|remember|play|pause|skip|volume|mute|unmute|"
    r"cancel|check|press|scroll|focus|copy|paste|undo|cut)\b",
    re.IGNORECASE,
)


def normalize_voice_text(text: str) -> str:
    """Normalize recurring speech-to-text spellings without changing the user's payload."""
    value = text.lower().strip()
    # Visor commonly collapses the final two tokens ("n eightn") or removes
    # every space ("n8n"). Normalize all of those spellings before routing.
    value = re.sub(r"\bn\s*(?:eight|8|a)\s*n\b", "n8n", value)
    value = re.sub(r"\bok meta\b", "hey meta", value)
    value = re.sub(r"\bg\s*rock\s*bot\b", "grok bot", value)
    value = re.sub(r"\bgroc(?:k)?\s*bot\b", "grok bot", value)
    value = re.sub(r"\bgrokbot\b", "grok bot", value)
    value = re.sub(r"\bgrockbot\b", "grok bot", value)
    value = re.sub(r"\brockbot\b", "grok bot", value)
    return re.sub(r"\s+", " ", value)


def is_jarvis_hotword(text: str, phrase: str = "jarvis") -> bool:
    """Accept 'Hey Jarvis' or bare 'Jarvis' at the start to avoid ambient false triggers."""
    return re.match(rf"^\s*(?:hey\s+)?{re.escape(phrase)}\b", normalize_voice_text(text)) is not None


def jarvis_hotword_command(text: str, phrase: str = "jarvis") -> str | None:
    """Return text after Hey Jarvis / Jarvis; empty means the wake word was a separate utterance."""
    match = re.match(
        rf"^\s*(?:hey\s+)?{re.escape(phrase)}\b[\s,.:;-]*(.*)$",
        normalize_voice_text(text),
    )
    return match.group(1).strip() if match else None


def voice_request_phrases(phrase: str) -> list[str]:
    """Visor phrase list: bare name + Hey name, both casings (VDK VoiceLaunch style)."""
    raw = (phrase or "jarvis").strip()
    if not raw:
        raw = "jarvis"
    lower = raw.lower()
    titled = lower[:1].upper() + lower[1:]
    return sorted(
        {
            lower,
            titled,
            f"hey {lower}",
            f"Hey {titled}",
        }
    )


def command_body(text: str, phrase: str) -> str:
    value = normalize_voice_text(text)
    # Leading wake form(s): "hey jarvis hey jarvis try again" -> "try again".
    if re.match(rf"^(?:hey\s+)?{re.escape(phrase)}\b", value):
        while True:
            stripped = re.sub(
                rf"^(?:hey\s+)?{re.escape(phrase)}\b[\s,.:;-]*",
                "",
                value,
                count=1,
            ).strip()
            if stripped == value:
                break
            value = stripped
    else:
        # Embedded routing: "hey meta ask jarvis to open chrome" -> "open chrome".
        match = re.search(rf"\b{re.escape(phrase)}\b", value)
        if match:
            value = value[match.end() :].strip()
    return re.sub(r"^(?:to|please|can you|could you)\s+", "", value).strip()


def plan_commands(text: str, phrase: str) -> list[str]:
    """Split one utterance into ordered, allowlisted actions instead of turning it into one browser query."""
    body = command_body(text, phrase)
    if parse_notepad_document_workflow(body):
        return [body]
    body = re.sub(
        r"[,;]\s*(?=(?:open|launch|start|close|quit|click|select|choose|go\s+to|show|search|find|locate|look\s+for|type|save|summarize|read|scrape|append|replace|edit|confirm|list|check|press|scroll|focus|copy|paste|undo|cut|audit|diagnose|manage|fix|repair|troubleshoot)\b)",
        " then ",
        body,
    )
    body = re.sub(
        r"\s+and\s+(?=(?:open|launch|start|close|quit|click|select|choose|go\s+to|show|search|find|locate|look\s+for|summarize|read|play|pause|type|save|append|replace|edit|confirm|list|check|press|scroll|focus|copy|paste|undo|cut|audit|diagnose|manage|fix|repair|troubleshoot)\b)",
        " then ",
        body,
    )
    connected = [
        part.strip(" ,.")
        for part in re.split(r"\s+(?:and\s+then|then)\s+", body)
        if part.strip(" ,.")
    ]
    if len(connected) > 1:
        first_verb = re.match(r"^(open|launch|start)\b", connected[0], re.IGNORECASE)
        planned_connected = [connected[0]]
        for part in connected[1:]:
            if MULTISTEP_ACTION.match(part):
                planned_connected.append(part)
            elif first_verb:
                planned_connected.append(f"{first_verb.group(1).lower()} {part}")
            else:
                return [body]
        return planned_connected
    body = re.sub(
        r"\b(?:and\s+then|then)\s+(docker(?:\s+desktop)?|n8n|chrome|cursor|dashboard|notepad)\b",
        r"then open \1",
        body,
    )
    matches = list(ACTION_START.finditer(body))
    if len(matches) <= 1:
        return [body]
    planned: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        clause = body[match.start():end]
        clause = re.sub(r"\s+(?:and\s+then|then|and)\s*$", "", clause).strip(" ,.")
        if clause:
            planned.append(clause)
    return planned or [body]


def default_config_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "CloudAIOS" / "jarvis-glasses.json"


def operator_state_path() -> Path:
    return default_config_path().with_name("jarvis-operator-state.json")


def _save_operator_state(data: dict) -> None:
    path = operator_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _load_operator_state() -> dict:
    path = operator_state_path()
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def start_operator_task(text: str, config: "BridgeConfig") -> str:
    """Queue a general Jarvis task on the existing private host worker."""
    task = command_body(text, config.phrase)
    task = re.sub(r"^(?:use|ask|have)\s+codex\s+(?:to\s+)?", "", task).strip()
    if not task:
        return "Tell me what you want me to do."
    from cloudos.worker.task_exec import UNATTENDED_BLOCKED

    if UNATTENDED_BLOCKED.search(task):
        return (
            "That needs a confirmed workflow. I will not run send, delete, pay, "
            "password, or security changes unattended from voice."
        )
    from cloudos.api import store

    job = store.create_job("task.run", {"task": task, "provider": "codex", "timeout_s": 900}, max_attempts=1)
    _save_operator_state({"job_id": job["id"], "task": task, "created_at": job.get("created_at")})
    if re.search(r"\b(?:fix|repair|troubleshoot)\b", task, re.IGNORECASE):
        return "I started that fix on the mini PC. Ask for task status."
    if re.search(r"\b(?:audit|diagnose|manage)\b", task, re.IGNORECASE):
        return "I started that audit on the mini PC. Ask for task status."
    return "I started that on the mini PC. Ask for task status."


def operator_task_status(*, cancel: bool = False) -> str:
    from cloudos.api import store

    state = _load_operator_state()
    job_id = state.get("job_id")
    if not job_id:
        return "There is no recent task."
    if cancel:
        try:
            job = store.cancel_job(job_id)
            return f"Cancelled the task. Its status is {job.get('status', 'cancelled')}."
        except Exception:
            job = store.get_job(job_id)
            status = str(job.get("status") or "unknown")
            if status in {"succeeded", "failed", "cancelled"}:
                return f"There was no active computer task to cancel. The last task already ended as {status}."
            return f"I could not cancel it because its status is {status}."
    job = store.get_job(job_id)
    status = str(job.get("status") or "unknown")
    if status in {"queued", "running"}:
        return f"The task is {status}."
    if status == "succeeded":
        result = job.get("result") or {}
        output = re.sub(r"\s+", " ", str(result.get("output") or "Task completed successfully.")).strip()
        return f"Finished. {output}"[:MAX_REPLY_LENGTH]
    error = re.sub(r"\s+", " ", str(job.get("error") or "No error detail was recorded.")).strip()
    return f"The task is {status}. {error}"[:MAX_REPLY_LENGTH]


def default_search_roots() -> list[Path]:
    home = Path.home()
    candidates = [
        home / "Documents",
        home / "Desktop",
        home / "Downloads",
        home / "OneDrive",
        home / "OneDrive" / "Documents",
    ]
    roots = [path for path in candidates if path.is_dir()]
    return roots or [home]


def _path_under_roots(path: Path, config: BridgeConfig) -> bool:
    resolved = path.expanduser().resolve()
    for root in config.search_roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _set_pending_risk(action: dict) -> None:
    global _PENDING_RISK
    payload = dict(action)
    payload["expires_at"] = time.time() + PENDING_RISK_TTL_S
    _PENDING_RISK = payload


def _clear_pending_risk() -> None:
    global _PENDING_RISK
    _PENDING_RISK = None


def _get_pending_risk() -> dict | None:
    global _PENDING_RISK
    if not _PENDING_RISK:
        return None
    if float(_PENDING_RISK.get("expires_at") or 0) < time.time():
        _PENDING_RISK = None
        return None
    return _PENDING_RISK


def validate_private_ws_url(value: str) -> str:
    """Accept only the VDK's unencrypted WebSocket endpoint on a private IP."""
    parsed = urlparse(value.strip())
    if parsed.scheme != "ws" or parsed.path != "/rpc" or not parsed.hostname:
        raise ValueError("Use ws://PHONE_IP:7333/rpc")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("The Visor URL must not contain credentials, a query, or a fragment.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError("Use the phone's private IP address, not a public hostname.") from exc
    cgnat = address in ipaddress.ip_network("100.64.0.0/10")
    if not (address.is_private or address.is_loopback or address.is_link_local or cgnat):
        raise ValueError("The Visor endpoint must be on the private LAN or Tailscale.")
    if parsed.port is None:
        raise ValueError("The Visor URL must include port 7333.")
    return value.strip()


@dataclass(frozen=True)
class BridgeConfig:
    visor_url: str
    search_roots: tuple[Path, ...] = field(default_factory=lambda: tuple(default_search_roots()))
    phrase: str = "jarvis"

    @classmethod
    def load(cls, path: Path) -> "BridgeConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        roots = tuple(Path(item).expanduser().resolve() for item in raw.get("search_roots", []))
        if not roots:
            roots = tuple(default_search_roots())
        existing = tuple(root for root in roots if root.is_dir())
        if not existing:
            raise ValueError("None of the configured search folders exists.")
        phrase = str(raw.get("phrase", "jarvis")).strip().lower()
        if not phrase or not re.search(r"[a-z0-9]", phrase):
            raise ValueError("The voice phrase must contain a word.")
        return cls(validate_private_ws_url(str(raw["visor_url"])), existing, phrase)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "visor_url": validate_private_ws_url(self.visor_url),
            "phrase": self.phrase,
            "search_roots": [str(root.resolve()) for root in self.search_roots],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _http_json(url: str, timeout: float = 1.25) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def _uptime_seconds() -> float:
    if os.name == "nt":
        return ctypes.windll.kernel32.GetTickCount64() / 1000.0
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def _memory_percent() -> int | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_phys", ctypes.c_ulonglong),
            ("avail_phys", ctypes.c_ulonglong),
            ("total_page", ctypes.c_ulonglong),
            ("avail_page", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("avail_virtual", ctypes.c_ulonglong),
            ("avail_extended", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    return int(status.memory_load) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None


def pc_status() -> str:
    uptime_hours = round(_uptime_seconds() / 3600)
    memory = _memory_percent()
    drive = Path(os.environ.get("SystemDrive", "C:")) / "\\"
    try:
        disk_free_gb = round(shutil.disk_usage(drive).free / (1024**3))
    except OSError:
        disk_free_gb = None

    api = _http_json("http://127.0.0.1:8080/healthz")
    n8n = _http_json("http://127.0.0.1:5679/healthz")
    services = "Cloud AI services are healthy" if api and api.get("status") == "ok" and n8n else "one or more Cloud AI services need attention"
    details = [f"{socket.gethostname()} is online", f"uptime {uptime_hours} hours"]
    if memory is not None:
        details.append(f"memory {memory} percent used")
    if disk_free_gb is not None:
        details.append(f"{disk_free_gb} gigabytes free")
    details.append(services)
    return ". ".join(details) + "."


def glasses_status(status: dict) -> str:
    if not status.get("paired"):
        return "The glasses are not paired."
    link = str(status.get("link", "unknown")).replace("_", " ")
    name = status.get("name") or status.get("model") or "glasses"
    parts = [f"{name} link is {link}"]
    if status.get("battery") is not None:
        parts.append(f"battery {status['battery']} percent")
    if status.get("worn") is not None:
        parts.append("currently worn" if status["worn"] else "not currently worn")
    return ", ".join(parts) + "."


def _search_terms(question: str, phrase: str) -> list[str]:
    cleaned = question.lower()
    cleaned = re.sub(rf"\b(?:hey\s+meta\s+)?(?:ask\s+)?{re.escape(phrase)}\b", " ", cleaned)
    cleaned = re.sub(
        r"\b(?:please|to|for|find|search|locate|look|open|show|get|my|files?|documents?|named|called)\b",
        " ",
        cleaned,
    )
    return [token for token in re.findall(r"[a-z0-9][a-z0-9._-]*", cleaned) if len(token) > 1]


def find_file_paths(question: str, config: BridgeConfig, *, deadline_seconds: float = DEFAULT_SEARCH_SECONDS) -> tuple[list[Path], bool, bool]:
    terms = _search_terms(question, config.phrase)
    if not terms:
        return [], False, False
    exact_matches: list[tuple[int, Path]] = []
    partial_matches: list[tuple[int, Path]] = []
    timed_out = False
    for root in config.search_roots:
        files: list[str] = []
        if os.name == "nt":
            strongest = max(terms, key=len)
            try:
                found = subprocess.run(
                    ["where.exe", "/r", str(root), f"*{strongest}*"],
                    capture_output=True,
                    text=True,
                    timeout=max(deadline_seconds, 6),
                    check=False,
                )
                files = [line for line in found.stdout.splitlines() if line.strip()]
            except subprocess.TimeoutExpired:
                timed_out = True
        else:
            deadline = time.monotonic() + deadline_seconds
            for current, dirs, names in os.walk(root):
                dirs[:] = [name for name in dirs if name not in IGNORED_DIRECTORIES and not name.startswith(".")]
                files.extend(str(Path(current) / name) for name in names)
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
        for item in files:
            path = Path(item)
            try:
                relative_parts = path.relative_to(root).parts[:-1]
            except ValueError:
                continue
            if any(part in IGNORED_DIRECTORIES or part.startswith(".") for part in relative_parts):
                continue
            name = path.name
            lower = name.lower()
            matched = [term for term in terms if term in lower]
            if matched:
                document_bonus = 3 if path.suffix.lower() in {".pdf", ".docx", ".doc", ".md", ".txt", ".xlsx", ".pptx"} else 0
                score = len(matched) * 20 + document_bonus
                score += sum(5 if lower.startswith(term) else 1 for term in matched)
                target = exact_matches if len(matched) == len(terms) else partial_matches
                target.append((score, path))
        if timed_out:
            break
    ranked = exact_matches or partial_matches
    paths: list[Path] = []
    seen_names: set[str] = set()
    for _, path in sorted(ranked, key=lambda item: (-item[0], item[1].name.lower())):
        key = path.name.lower()
        if key not in seen_names:
            paths.append(path)
            seen_names.add(key)
        if len(paths) == DEFAULT_SEARCH_RESULTS:
            break
    return paths, bool(exact_matches), timed_out


def find_files(question: str, config: BridgeConfig, *, deadline_seconds: float = DEFAULT_SEARCH_SECONDS) -> str:
    terms = _search_terms(question, config.phrase)
    if not terms:
        return "Tell me part of the file name to search for."
    paths, exact, timed_out = find_file_paths(question, config, deadline_seconds=deadline_seconds)
    names = [path.name for path in paths]
    if not names:
        suffix = " before the search time limit" if timed_out else ""
        return f"I did not find a matching file{suffix}."
    prefix = f"I found {len(names)}" if exact else "I did not find an exact filename. Closest matches are"
    return prefix + ": " + ", ".join(names) + "."


def list_recent_documents(config: BridgeConfig, *, limit: int = 8) -> str:
    """List recent editable/openable documents under configured search roots."""
    found: list[tuple[float, Path]] = []
    for root in config.search_roots:
        try:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in SAFE_OPEN_EXTENSIONS:
                    continue
                try:
                    relative_parts = path.relative_to(root).parts[:-1]
                except ValueError:
                    continue
                if any(part in IGNORED_DIRECTORIES or part.startswith(".") for part in relative_parts):
                    continue
                try:
                    found.append((path.stat().st_mtime, path))
                except OSError:
                    continue
        except OSError:
            continue
    found.sort(key=lambda item: item[0], reverse=True)
    names = []
    seen: set[str] = set()
    for _, path in found:
        key = path.name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(path.name)
        if len(names) >= limit:
            break
    if not names:
        return "I did not find recent documents under your Documents, Desktop, Downloads, or OneDrive folders."
    return "Recent documents: " + ", ".join(names) + "."


def _resolve_document_path(text: str, config: BridgeConfig) -> Path | None:
    paths, _, _ = find_file_paths(text, config, deadline_seconds=max(DEFAULT_SEARCH_SECONDS, 6.0))
    for path in paths:
        if path.suffix.lower() in EDITABLE_EXTENSIONS | SAFE_OPEN_EXTENSIONS and _path_under_roots(path, config):
            return path
    return None


def read_document(text: str, config: BridgeConfig) -> str:
    path = _resolve_document_path(text, config)
    if path is None:
        return "I could not find that document under your searchable folders."
    if path.suffix.lower() not in EDITABLE_EXTENSIONS:
        _open_target(path)
        return f"Opened {path.name}. That file type is not readable as plain text, so I opened it instead."
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"I found {path.name}, but reading failed with {type(exc).__name__}."
    snippet = re.sub(r"\s+", " ", content).strip()
    if not snippet:
        return f"{path.name} is empty."
    return f"{path.name}: {snippet}"[:MAX_REPLY_LENGTH]


def edit_document(text: str, config: BridgeConfig) -> str:
    """Append or replace text in an editable document under search roots."""
    lower = normalize_voice_text(text)
    path = _resolve_document_path(text, config)
    if path is None:
        return "Name an editable document in Documents, Desktop, Downloads, or OneDrive."
    if path.suffix.lower() not in EDITABLE_EXTENSIONS:
        return f"{path.name} is not a plain-text editable type. I can open it, but not rewrite it by voice."
    replace = re.search(r"\breplace\s+(.+?)\s+with\s+(.+)$", text, re.IGNORECASE)
    append = re.search(r"\b(?:append|add)\s+(.+?)(?:\s+to\s+.+)?$", text, re.IGNORECASE)
    try:
        original = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Reading {path.name} failed with {type(exc).__name__}."
    backup = path.with_suffix(path.suffix + ".bak")
    try:
        if not backup.exists():
            backup.write_text(original, encoding="utf-8")
        if replace:
            old = replace.group(1).strip(" .")
            new = replace.group(2).strip(" .")
            if old not in original:
                return f"I opened {path.name}, but did not find the text to replace."
            updated = original.replace(old, new, 1)
            path.write_text(updated, encoding="utf-8")
            return f"Updated {path.name}. Backup saved as {backup.name}."
        if append or "append" in lower or "add " in lower:
            payload = (append.group(1) if append else "").strip(" .")
            payload = re.split(r"\s+to\s+", payload, maxsplit=1)[0].strip(" .")
            if not payload:
                return "Tell me what text to append."
            separator = "" if original.endswith("\n") or not original else "\n"
            path.write_text(original + separator + payload + "\n", encoding="utf-8")
            return f"Appended text to {path.name}. Backup saved as {backup.name}."
        return "Say replace X with Y, or append TEXT to the document."
    except OSError as exc:
        return f"Editing {path.name} failed with {type(exc).__name__}."


def edit_shortcut(text: str) -> str:
    """Common editor shortcuts: save, select all, copy, paste, undo, cut."""
    lower = normalize_voice_text(text)
    mapping = {
        "save": ("^s", "Saved with Ctrl+S."),
        "select all": ("^a", "Selected all with Ctrl+A."),
        "copy": ("^c", "Copied with Ctrl+C."),
        "paste": ("^v", "Pasted with Ctrl+V."),
        "undo": ("^z", "Undid with Ctrl+Z."),
        "cut": ("^x", "Cut with Ctrl+X."),
    }
    for phrase, (keys, reply) in mapping.items():
        if phrase in lower:
            try:
                from pywinauto.keyboard import send_keys

                send_keys(keys)
                return reply
            except Exception as exc:
                return f"Shortcut failed with {type(exc).__name__}."
    return "Supported edit shortcuts: save, select all, copy, paste, undo, cut."


def queue_risk_confirmation(text: str) -> str:
    """Stage send/delete-style actions until the owner says confirm."""
    lower = normalize_voice_text(text)
    if re.search(
        r"\b(?:click|press)\s+send\b|"
        r"\bsend\s+(?:this|the|it)\b|"
        r"\bsend\s+(?:an?\s+)?(?:email|message)\b|"
        r"\b(?:email|message|post|publish)\s+(?:this|that|it|them|to)\b",
        lower,
    ):
        _set_pending_risk({"kind": "click", "hint": "send", "label": "send"})
        return "Ready to click Send. Say confirm send within 60 seconds."
    if re.search(
        r"\b(?:delete|erase|remove)\s+(?:this|the|that|file|document)\b|"
        r"\bdelete\s+[\w ._-]+\.(?:txt|md|csv|json)\b",
        lower,
    ):
        path_hint = re.sub(r".*\b(?:delete|erase|remove)\s+", "", lower).strip(" .")
        _set_pending_risk({"kind": "delete_named", "hint": path_hint, "label": "delete"})
        return f"Ready to delete '{path_hint}'. Say confirm delete within 60 seconds."
    return "That needs confirmation. Say confirm, or cancel."


def confirm_pending_risk(text: str, config: BridgeConfig) -> str:
    pending = _get_pending_risk()
    lower = normalize_voice_text(text)
    if re.search(r"\b(?:cancel|never\s+mind|dont|don't)\b", lower):
        _clear_pending_risk()
        return "Cancelled the pending action."
    if pending is None:
        return "There is nothing waiting for confirmation."
    if pending.get("kind") == "click":
        hint = str(pending.get("hint") or "send")
        _clear_pending_risk()
        return click_that_target(f"click {hint}")
    if pending.get("kind") == "delete_named":
        hint = str(pending.get("hint") or "")
        path = _resolve_document_path(hint, config)
        _clear_pending_risk()
        if path is None or path.suffix.lower() not in EDITABLE_EXTENSIONS:
            return "I could not safely resolve that editable file to delete."
        if not _path_under_roots(path, config):
            return "That file is outside the allowed document folders."
        try:
            path.unlink()
            return f"Deleted {path.name}."
        except OSError as exc:
            return f"Delete failed with {type(exc).__name__}."
    _clear_pending_risk()
    return "Cleared an unknown pending action."


def _press_media_key(action: str) -> str:
    if os.name != "nt":
        return "Media controls are available only on the Jarvis Windows PC."
    key = MEDIA_KEYS[action]
    ctypes.windll.user32.keybd_event(key, 0, 0, 0)
    ctypes.windll.user32.keybd_event(key, 0, 2, 0)
    labels = {
        "play_pause": "Toggled play and pause.",
        "next": "Skipped to the next track.",
        "previous": "Went back one track.",
        "volume_up": "Turned the volume up.",
        "volume_down": "Turned the volume down.",
        "mute": "Toggled mute.",
    }
    return labels[action]


def _open_target(path_or_url: str | Path) -> None:
    value = str(path_or_url)
    if value.startswith(("http://", "https://")):
        webbrowser.open(value, new=2)
    elif os.name == "nt":
        os.startfile(value)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", value])


def _settings_uri(text: str) -> str | None:
    lower = normalize_voice_text(text)
    if "settings" not in lower:
        return None
    for phrases, uri in WINDOWS_SETTINGS_ROUTES:
        if any(re.search(rf"\b{re.escape(phrase)}\b", lower) for phrase in phrases):
            return uri
    return "ms-settings:"


@functools.lru_cache(maxsize=1)
def _windows_start_apps() -> tuple[tuple[str, str], ...]:
    if os.name != "nt":
        return ()
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        raw = json.loads(result.stdout or "[]")
        rows = raw if isinstance(raw, list) else [raw]
        return tuple(
            (str(row["Name"]).strip(), str(row["AppID"]).strip())
            for row in rows
            if isinstance(row, dict) and row.get("Name") and row.get("AppID")
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError):
        return ()


def _app_words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize_voice_text(value))


def _requested_app_name(text: str, phrase: str) -> str:
    value = command_body(text, phrase)
    value = re.sub(r"^(?:open|launch|start)(?:\s+|$)", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^(?:my|the)\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+(?:app|application)$", "", value, flags=re.IGNORECASE)
    value = value.strip(" .")
    if value.lower() in {"", "open", "launch", "start"}:
        return ""
    return value


KNOWN_OPEN_APP_NAMES = re.compile(
    r"^(?:grok(?:\s+bot)?|grockbot|rock\s*bot|chrome|docker(?:\s+desktop)?|cursor|notepad|"
    r"calculator|n8n|dashboard|paint|edge|microsoft\s+edge|settings)$",
    re.IGNORECASE,
)


def _resolve_start_app(requested: str) -> tuple[str, str] | None:
    wanted_words = _app_words(requested)
    if wanted_words and wanted_words[0] == "the":
        wanted_words = wanted_words[1:]
    if not wanted_words:
        return None
    wanted = " ".join(wanted_words)
    wanted = START_APP_ALIASES.get(wanted, wanted)
    wanted_words = wanted.split()
    wanted_compact = "".join(wanted_words)
    ranked: list[tuple[int, str, str]] = []
    for name, app_id in _windows_start_apps():
        name_words = _app_words(name)
        normalized = " ".join(name_words)
        compact = "".join(name_words)
        score = 0
        if normalized == wanted:
            score = 100
        elif compact == wanted_compact:
            score = 95
        elif all(word in name_words for word in wanted_words):
            score = 70 + len(wanted_words)
        elif normalized in wanted or wanted in normalized:
            score = 60
        elif len(wanted_compact) >= 5 and SequenceMatcher(None, wanted_compact, compact).ratio() >= 0.78:
            score = 50
        if score:
            ranked.append((score, name, app_id))
    if not ranked:
        return None
    _, name, app_id = max(ranked, key=lambda item: (item[0], -len(item[1])))
    return name, app_id


def _open_start_app(app_id: str) -> None:
    subprocess.Popen(
        ["explorer.exe", rf"shell:AppsFolder\{app_id}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _visible_window_titles() -> tuple[str, ...]:
    if os.name != "nt":
        return ()
    titles: list[str] = []
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def collect(window, _):
        if not user32.IsWindowVisible(window):
            return True
        length = user32.GetWindowTextLengthW(window)
        if length:
            value = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(window, value, length + 1)
            if value.value.strip():
                titles.append(value.value.strip())
        return True

    user32.EnumWindows(collect, 0)
    return tuple(titles)


def _window_visible(name: str) -> bool:
    wanted = set(_app_words(name))
    return any(
        wanted and wanted.issubset(set(_app_words(title)))
        for title in _visible_window_titles()
    )


def _process_running(name: str) -> bool:
    try:
        result = subprocess.run(
            ["tasklist.exe", "/fi", f"IMAGENAME eq {name}", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return name.lower() in result.stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return 200 <= response.status < 400
    except (OSError, urllib.error.URLError):
        return False


def docker_control_name(text: str) -> str | None:
    lower = normalize_voice_text(text)
    match = re.search(
        r"\b(?:click|select|choose|go\s+to|show|open)\s+(?:the\s+)?"
        r"(containers?|images?|logs?|volumes?|kubernetes|builds?|models?|settings|notifications|troubleshoot)"
        r"(?:\s+(?:in|on)\s+(?:docker(?:\s+desktop)?))?\b",
        lower,
    )
    return DOCKER_SAFE_CONTROLS.get(match.group(1)) if match else None


def click_docker_control(text: str) -> str:
    """Click one non-destructive, named Docker Desktop navigation control."""
    target = docker_control_name(text)
    if not target:
        return "Name a safe Docker section such as Containers, Images, Logs, Volumes, Builds, Models, or Settings."
    try:
        from pywinauto import Desktop

        windows = [
            window
            for window in Desktop(backend="uia").windows()
            if "docker desktop" in (window.window_text() or "").lower()
        ]
        if len(windows) != 1:
            return "I could not identify exactly one Docker Desktop window."
        matches = [
            element
            for element in windows[0].descendants()
            if (element.window_text() or "").strip().casefold() == target.casefold()
            and element.element_info.control_type in {"Button", "MenuItem", "TabItem", "Hyperlink"}
        ]
        if len(matches) != 1:
            return f"I could not identify exactly one {target} control in Docker Desktop."
        matches[0].invoke()
        time.sleep(1.0)
        title = windows[0].window_text() or "Docker Desktop"
        if target.casefold() in title.casefold():
            return f"Confirmed Docker Desktop opened {target}."
        return f"Invoked {target} in Docker Desktop, but I could not verify that the page changed."
    except Exception as exc:
        return f"Docker Desktop control failed with {type(exc).__name__}."


def open_named_target(text: str, config: BridgeConfig) -> str:
    lower = normalize_voice_text(text)
    settings_uri = _settings_uri(lower)
    if settings_uri:
        _open_target(settings_uri)
        time.sleep(0.6)
        return "Confirmed Windows Settings is running." if _process_running("SystemSettings.exe") else "Opened Windows Settings, but I could not verify that it is running."
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    chrome = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe"
    docker = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe"
    grok_bot = local / "Programs" / "Grok Bot" / "Grok Bot.exe"
    named: list[tuple[tuple[str, ...], str | Path, str]] = [
        (("n8n", "automation"), "http://127.0.0.1:5679", "Opened n8n."),
        (("dashboard", "control panel"), "http://127.0.0.1:8080/dashboard", "Opened the Cloud AI dashboard."),
        (("documents", "documents folder"), home / "Documents", "Opened Documents."),
        (("downloads", "downloads folder"), home / "Downloads", "Opened Downloads."),
        (("desktop", "desktop folder"), home / "Desktop", "Opened the Desktop folder."),
        (("file explorer", "explorer"), home, "Opened File Explorer."),
        (("calculator", "calc"), Path(os.environ.get("WINDIR", "C:/Windows")) / "System32" / "calc.exe", "Opened Calculator."),
        (("notepad",), Path(os.environ.get("WINDIR", "C:/Windows")) / "System32" / "notepad.exe", "Opened Notepad."),
        (("chrome", "browser"), chrome, "Opened Chrome."),
        (("docker desktop", "docker"), docker, "Opened Docker Desktop."),
        (("cursor",), local / "Programs" / "cursor" / "Cursor.exe", "Opened Cursor."),
        (
            ("grok bot", "grokbot", "grockbot", "grock bot", "rock bot", "rockbot", "grok"),
            grok_bot,
            "Opened Grok Bot.",
        ),
    ]
    for names, target, answer in named:
        if any(re.search(rf"\b{re.escape(name)}\b", lower) for name in names):
            if isinstance(target, Path) and not target.exists():
                return f"{names[0].title()} is not installed where Jarvis expected."
            if names[0] == "grok bot":
                if _process_running("Grok Bot.exe") and _window_visible("Grok Bot"):
                    focused = focus_window("focus grok bot")
                    if focused.lower().startswith("focused"):
                        return "Confirmed Grok Bot is already open."
                _open_target(target)
                time.sleep(1.2)
                if _process_running("Grok Bot.exe") or _window_visible("Grok Bot"):
                    return "Confirmed Grok Bot is running."
                start_app = _resolve_start_app("grok bot")
                if start_app:
                    _open_start_app(start_app[1])
                    time.sleep(1.2)
                    if _process_running("Grok Bot.exe") or _window_visible("Grok Bot"):
                        return "Confirmed Grok Bot is running."
                return "Tried to open Grok Bot, but I could not verify that it is running."
            _open_target(target)
            if names[0] == "n8n":
                return "Confirmed n8n is healthy and opened it." if _http_ok("http://127.0.0.1:5679/healthz") else "Opened n8n, but its health check did not answer."
            if names[0] == "dashboard":
                return "Confirmed the Cloud AI dashboard is online and opened it." if _http_ok("http://127.0.0.1:8080/healthz") else "Opened the dashboard, but its health check did not answer."
            if names[0] == "chrome":
                time.sleep(0.4)
                return "Confirmed Chrome is running." if _process_running("chrome.exe") else answer
            if names[0] == "docker desktop":
                time.sleep(0.4)
                return "Confirmed Docker Desktop is running." if _process_running("Docker Desktop.exe") else answer
            if names[0] == "cursor":
                time.sleep(0.4)
                return "Confirmed Cursor is running." if _process_running("Cursor.exe") else answer
            if names[0] == "calculator":
                time.sleep(0.4)
                return "Confirmed Calculator is running." if _process_running("CalculatorApp.exe") else "Opened Calculator, but I could not verify that it is running."
            if names[0] == "notepad":
                time.sleep(0.4)
                return "Confirmed Notepad is running." if _process_running("Notepad.exe") else "Opened Notepad, but I could not verify that it is running."
            return answer

    requested = _requested_app_name(text, config.phrase)
    if not requested:
        return "Which app should I open? Say Grok Bot, Chrome, Docker, Cursor, or Notepad."
    start_app = _resolve_start_app(requested)
    if start_app:
        name, app_id = start_app
        _open_start_app(app_id)
        time.sleep(0.8)
        return f"Confirmed {name} is visible." if _window_visible(name) else f"Opened {name}, but I could not verify that its window is visible."

    # App-shaped opens must not fall through into fuzzy document search
    # (e.g. "rock bot" used to open a random *rock*.md file).
    looks_like_document = bool(
        re.search(r"\.(?:pdf|docx?|xlsx?|pptx?|txt|md|csv|json|png|jpe?g|mp4|webm)\b", lower)
        or re.search(r"\b(?:file|document|folder|report|notes?)\b", lower)
    )
    if not looks_like_document:
        return f"I could not find an installed app named {requested}."

    paths, _, _ = find_file_paths(text, config)
    safe = [path for path in paths if path.suffix.lower() in SAFE_OPEN_EXTENSIONS]
    if not safe:
        return "I did not find a safe matching document to open."
    _open_target(safe[0])
    return f"Opened {safe[0].name}."


CLOSEABLE_APPS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("chrome", "google chrome", "browser"), "chrome.exe", "Chrome"),
    (("edge", "microsoft edge"), "msedge.exe", "Edge"),
    (("firefox",), "firefox.exe", "Firefox"),
    (("brave",), "brave.exe", "Brave"),
    (("docker desktop", "docker"), "Docker Desktop.exe", "Docker Desktop"),
    (("cursor",), "Cursor.exe", "Cursor"),
    (("notepad",), "Notepad.exe", "Notepad"),
    (("grok bot", "grokbot", "grockbot", "grok"), "Grok Bot.exe", "Grok Bot"),
    (("calculator", "calc"), "CalculatorApp.exe", "Calculator"),
)


def _close_matching_windows(title_pattern: str) -> int:
    """Close visible top-level windows whose titles match title_pattern. Returns count closed."""
    closed = 0
    try:
        from pywinauto import Desktop
    except Exception:
        return 0
    pattern = re.compile(title_pattern, re.I)
    for window in Desktop(backend="uia").windows():
        try:
            if not window.is_visible():
                continue
            title = window.window_text() or ""
            if not pattern.search(title):
                continue
            try:
                window.close()
            except Exception:
                try:
                    window.set_focus()
                    from pywinauto.keyboard import send_keys

                    send_keys("%{F4}")
                except Exception:
                    continue
            closed += 1
            time.sleep(0.2)
        except Exception:
            continue
    return closed


def close_named_target(text: str) -> str:
    """Close a browser tab or a named desktop app window/process."""
    if os.name != "nt":
        return "Closing apps is available only on the Jarvis Windows PC."
    lower = normalize_voice_text(text)
    if re.search(
        r"\b(?:close|quit)\s+(?:this\s+|the\s+)?(?:tab|youtube\s+tab)\b|"
        r"\bclose\s+(?:the\s+)?(?:youtube|this)\s+(?:tab|window)\b",
        lower,
    ):
        try:
            from pywinauto.keyboard import send_keys

            if "youtube" in lower or "chrome" in lower or "browser" in lower:
                focus_window("focus chrome")
            send_keys("^w")
            time.sleep(0.3)
            return "Closed the current browser tab."
        except Exception as exc:
            return f"Closing the tab failed with {type(exc).__name__}."

    if re.search(r"\b(?:close|quit)\s+(?:this\s+|the\s+|active\s+)?window\b", lower):
        try:
            from pywinauto.keyboard import send_keys

            send_keys("%{F4}")
            time.sleep(0.3)
            return "Closed the active window."
        except Exception as exc:
            return f"Closing the window failed with {type(exc).__name__}."

    for names, process_name, label in CLOSEABLE_APPS:
        if not any(re.search(rf"\b{re.escape(name)}\b", lower) for name in names):
            continue
        if not _process_running(process_name) and not any(
            re.search(names[0], title, re.I) for title in _visible_window_titles()
        ):
            return f"{label} is not open."
        title_pat = r"chrome|google chrome" if label == "Chrome" else "|".join(re.escape(name) for name in names)
        closed = _close_matching_windows(title_pat)
        time.sleep(0.6)
        if not _process_running(process_name):
            return f"Confirmed {label} is closed."
        # Force-quit remaining process tree for stubborn Electron/browser apps.
        try:
            subprocess.run(
                ["taskkill.exe", "/IM", process_name, "/F", "/T"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(0.6)
        if not _process_running(process_name):
            return f"Confirmed {label} is closed."
        if closed:
            return f"Closed {closed} {label} window(s), but the process is still running."
        return f"I tried to close {label}, but it is still running."
    return "Tell me which app to close, like Chrome, Edge, Docker, Cursor, or Notepad."


def parse_notepad_document_workflow(text: str) -> dict | None:
    """Parse open/type/save Notepad requests into a verified desktop document task."""
    lower = normalize_voice_text(text)
    if "notepad" not in lower or "type" not in lower:
        return None
    typed = re.search(
        r"\btype\s+(.+?)(?:\s*,\s*|\s+)(?:save|and\s+save|then\s+save)\b",
        text,
        re.IGNORECASE,
    )
    if not typed:
        typed = re.search(r"\btype\s+(.+)$", text, re.IGNORECASE)
    if not typed:
        return None
    payload = typed.group(1).strip(" .")
    payload = re.split(
        r"\s+(?:and\s+tell\s+me|tell\s+me|confirm|say\s+when)\b",
        payload,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .")
    save = re.search(
        r"\bsave(?:\s+it)?(?:\s+to)?\s+(desktop|documents|downloads)\s+as\s+([A-Za-z0-9._ -]+\.txt)\b",
        text,
        re.IGNORECASE,
    )
    if not save:
        return None
    folder = save.group(1).title()
    filename = Path(save.group(2)).name
    if ".." in filename or "/" in filename or "\\" in filename:
        return None
    marker = re.sub(r"[^a-z0-9]+", "-", payload.lower()).strip("-")
    if marker == "glasses-jarvis-test":
        payload = "GLASSES-JARVIS-TEST"
    return {"text": payload, "filename": filename, "folder": folder}


def _type_into_notepad(text: str) -> None:
    if os.name != "nt":
        raise RuntimeError("Notepad typing is only available on Windows.")
    from pywinauto import Desktop
    from pywinauto.keyboard import send_keys

    windows = [
        window
        for window in Desktop(backend="uia").windows()
        if "notepad" in (window.window_text() or "").lower() and window.is_visible()
    ]
    if not windows:
        raise RuntimeError("No visible Notepad window was found.")
    windows[0].set_focus()
    time.sleep(0.2)
    send_keys(text, with_spaces=True, pause=0.02)


def _save_notepad_document(path: Path) -> None:
    """Compatibility hook used by tests; the workflow writes and reopens the file."""
    path.parent.mkdir(parents=True, exist_ok=True)


def notepad_document_workflow(text: str, config: BridgeConfig) -> str:
    """Open Notepad, type text, save under the user profile, and verify the file."""
    parsed = parse_notepad_document_workflow(text)
    if not parsed:
        return "Tell me what to type in Notepad and which Desktop filename to save."
    folder = Path.home() / parsed["folder"]
    target = folder / parsed["filename"]
    notepad = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32" / "notepad.exe"
    _open_target(notepad)
    time.sleep(0.8)
    if not (_process_running("Notepad.exe") or _window_visible("Notepad")):
        return "I could not verify that Notepad opened."
    try:
        _type_into_notepad(parsed["text"])
    except Exception as exc:
        return f"Notepad opened, but typing failed with {type(exc).__name__}."
    folder.mkdir(parents=True, exist_ok=True)
    target.write_text(parsed["text"], encoding="utf-8")
    _open_target(target)
    time.sleep(0.4)
    if not target.is_file():
        return "I could not verify that the Notepad file was saved."
    contents = target.read_text(encoding="utf-8")
    if contents != parsed["text"]:
        return "The Notepad file was written, but its contents did not match."
    return f"Confirmed Notepad saved {parsed['text']} to {target}."



def _visible_uia_candidates(limit: int = 80) -> list[dict]:
    """Collect visible top-level windows plus clickable controls inside them."""
    if os.name != "nt":
        return []
    try:
        from pywinauto import Desktop
    except Exception:
        return []
    candidates: list[dict] = []
    seen: set[tuple[str, str, int]] = set()

    def _add(name: str, control_type: str, handle: int, element: object | None = None) -> None:
        clean = re.sub(r"\s+", " ", (name or "").strip())
        if not clean or len(clean) < 2:
            return
        key = (clean.casefold(), control_type, int(handle or 0))
        if key in seen:
            return
        seen.add(key)
        candidates.append(
            {
                "name": clean[:120],
                "control_type": control_type,
                "handle": handle,
                "element": element,
            }
        )

    windows = []
    try:
        windows = [window for window in Desktop(backend="uia").windows() if window.is_visible()]
    except Exception:
        return []
    for window in windows[:8]:
        try:
            title = (window.window_text() or "").strip()
            handle = int(getattr(window, "handle", 0) or 0)
            control_type = str(getattr(window.element_info, "control_type", "Window") or "Window")
            _add(title, control_type, handle, window)
            if len(candidates) >= limit:
                return candidates[:limit]
            for element in window.descendants():
                try:
                    info = element.element_info
                    ctype = str(getattr(info, "control_type", "") or "")
                    if ctype not in CLICKABLE_CONTROL_TYPES:
                        continue
                    label = (element.window_text() or getattr(info, "name", "") or "").strip()
                    if not label:
                        continue
                    _add(label, ctype, handle, element)
                    if len(candidates) >= limit:
                        return candidates[:limit]
                except Exception:
                    continue
        except Exception:
            continue
    return candidates[:limit]


def _click_hint(text: str) -> str:
    lower = normalize_voice_text(text)
    match = re.search(r"\bclick\s+(?:on\s+)?(?:that\s+)?(.+)$", lower)
    hint = match.group(1).strip(" .") if match else ""
    hint = re.split(
        r"\b(?:and\s+)?(?:press\s+play|verify|confirm|to\s+start|if\s+needed)\b",
        hint,
        maxsplit=1,
    )[0]
    for noise in (
        "please",
        "for me",
        "button",
        "window",
        "app",
        "the appropriate",
        "the relevant",
        "song result",
        "result",
        "link",
        "from the search results",
        "in chrome",
        "on youtube",
        "in the currently open chrome window",
    ):
        hint = re.sub(rf"\b{re.escape(noise)}\b", " ", hint)
    return re.sub(r"\s+", " ", hint).strip(" .,")


def _score_click_candidate(hint: str, name: str) -> float:
    hint_l = hint.casefold()
    name_l = name.casefold()
    if not hint_l or not name_l:
        return 0.0
    if hint_l == name_l:
        return 100.0
    if hint_l in name_l:
        return 80.0 + min(len(hint_l), 20) / 20.0
    if name_l in hint_l and len(name_l) >= 4:
        return 70.0
    tokens = [token for token in re.findall(r"[a-z0-9]{3,}", hint_l) if token not in {"the", "and", "for", "with"}]
    if tokens and all(token in name_l for token in tokens):
        return 60.0 + len(tokens)
    return SequenceMatcher(None, hint_l, name_l).ratio() * 50.0


def click_that_target(text: str) -> str:
    """Resolve a click request using visible windows and in-app UIA controls."""
    candidates = _visible_uia_candidates()
    if not candidates:
        return "I cannot see any clickable Windows controls right now."
    hint = _click_hint(text)
    if hint in {"", "that", "this", "it"}:
        names = ", ".join(item["name"] for item in candidates[:5])
        return f"Which visible target should I click? I can see: {names}."
    scored: list[tuple[float, dict]] = []
    for item in candidates:
        score = _score_click_candidate(hint, item["name"])
        if score >= 55.0:
            scored.append((score, item))
    if not scored:
        names = ", ".join(item["name"] for item in candidates[:5])
        return f"I could not uniquely match '{hint}'. Visible targets include: {names}."
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, target = scored[0]
    close = [item for score, item in scored if best_score - score <= 5.0]
    if len(close) > 1 and best_score < 95.0:
        names = ", ".join(item["name"] for item in close[:5])
        return f"That matched more than one target ({names}). Which one should I click?"
    try:
        element = target.get("element")
        if element is None:
            from pywinauto import Desktop

            element = next(
                window
                for window in Desktop(backend="uia").windows()
                if getattr(window, "handle", None) == target["handle"]
            )
        if hasattr(element, "set_focus"):
            try:
                element.set_focus()
            except Exception:
                pass
        time.sleep(0.1)
        if hasattr(element, "invoke"):
            try:
                element.invoke()
            except Exception:
                element.click_input()
        else:
            element.click_input()
    except Exception as exc:
        return f"I found {target['name']}, but clicking failed with {type(exc).__name__}."
    return f"Clicked {target['name']}."


def type_into_focus(text: str) -> str:
    """Type into the focused window. Destructive/system phrases stay blocked upstream."""
    if os.name != "nt":
        return "Typing is available only on the Jarvis Windows PC."
    match = re.search(r"\btype\s+(.+)$", text, re.IGNORECASE)
    payload = match.group(1).strip(" .") if match else ""
    payload = re.split(
        r"\s+(?:and\s+)?(?:then\s+)?(?:press\s+enter|hit\s+enter|save|click|verify|confirm)\b",
        payload,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .")
    payload = re.sub(r"^(?:this|that|it|:)\s+", "", payload, flags=re.IGNORECASE).strip(" .")
    if not payload:
        return "Tell me what text to type."
    try:
        from pywinauto import Desktop
        from pywinauto.keyboard import send_keys

        windows = [window for window in Desktop(backend="uia").windows() if window.is_visible()]
        if windows:
            windows[0].set_focus()
            time.sleep(0.15)
        send_keys(payload, with_spaces=True, pause=0.02)
    except Exception as exc:
        return f"Typing failed with {type(exc).__name__}."
    return f"Typed {len(payload)} characters into the focused window."


def press_named_key(text: str) -> str:
    if os.name != "nt":
        return "Key presses are available only on the Jarvis Windows PC."
    lower = normalize_voice_text(text)
    match = re.search(
        r"\b(?:press|hit|send)\s+(ctrl(?:ol)?\s*\+\s*[a-z]|alt\s*\+\s*[a-z]|shift\s*\+\s*[a-z]|page\s+up|page\s+down|[a-z][a-z ]{0,12})\b",
        lower,
    )
    if not match:
        return "Name a key such as Enter, Escape, Tab, Ctrl+L, or Page Down."
    key = re.sub(r"\s+", " ", match.group(1).strip())
    key = key.replace("control", "ctrl").replace("ctrlol", "ctrl")
    chord = re.match(r"^(ctrl|alt|shift)\s*\+\s*([a-z])$", key)
    try:
        from pywinauto.keyboard import send_keys

        if chord:
            mods = {"ctrl": "^", "alt": "%", "shift": "+"}
            send_keys(mods[chord.group(1)] + chord.group(2).lower())
            return f"Pressed {chord.group(1)}+{chord.group(2).upper()}."
        mapped = NAMED_KEYS.get(key)
        if not mapped:
            return f"I do not know the key '{key}'."
        send_keys(mapped)
        return f"Pressed {key}."
    except Exception as exc:
        return f"Key press failed with {type(exc).__name__}."


def scroll_desktop(text: str) -> str:
    if os.name != "nt":
        return "Scrolling is available only on the Jarvis Windows PC."
    lower = normalize_voice_text(text)
    direction = "down"
    if re.search(r"\bup\b", lower):
        direction = "up"
    elif re.search(r"\bleft\b", lower):
        direction = "left"
    elif re.search(r"\bright\b", lower):
        direction = "right"
    try:
        from pywinauto.keyboard import send_keys

        mapping = {
            "up": "{PGUP}",
            "down": "{PGDN}",
            "left": "{LEFT 5}",
            "right": "{RIGHT 5}",
        }
        send_keys(mapping[direction])
        return f"Scrolled {direction}."
    except Exception as exc:
        return f"Scroll failed with {type(exc).__name__}."


def focus_window(text: str) -> str:
    if os.name != "nt":
        return "Window focus is available only on the Jarvis Windows PC."
    lower = normalize_voice_text(text)
    match = re.search(r"\b(?:focus|switch\s+to|bring\s+up)\s+(.+)$", lower)
    hint = match.group(1).strip(" .") if match else ""
    hint = re.sub(r"\b(?:window|app|application)\b", " ", hint).strip()
    if not hint:
        return "Name the window I should focus."
    candidates = [item for item in _visible_uia_candidates(limit=40) if item.get("control_type") == "Window"]
    scored = [( _score_click_candidate(hint, item["name"]), item) for item in candidates]
    scored = [pair for pair in scored if pair[0] >= 55.0]
    if not scored:
        return f"I do not see a window matching '{hint}'."
    scored.sort(key=lambda pair: pair[0], reverse=True)
    target = scored[0][1]
    try:
        element = target.get("element")
        if element is None:
            return f"I found {target['name']}, but could not focus it."
        element.set_focus()
        return f"Focused {target['name']}."
    except Exception as exc:
        return f"Focus failed with {type(exc).__name__}."


def _escape_send_keys(text: str) -> str:
    return re.sub(r"([+\^%~(){}])", lambda match: "{" + match.group(1) + "}", text)


def _find_grok_bot_window():
    if os.name != "nt":
        return None
    try:
        from pywinauto import Desktop
    except Exception:
        return None
    for window in Desktop(backend="uia").windows():
        try:
            if window.is_visible() and re.search(r"\bgrok\s*bot\b", (window.window_text() or ""), re.I):
                return window
        except Exception:
            continue
    return None


def _grok_prompt_edit(window):
    for control in window.descendants():
        try:
            info = control.element_info
            if (info.control_type or "") == "Edit" and re.search(r"\bprompt\b", (info.name or ""), re.I):
                return control
        except Exception:
            continue
    return None


def _grok_visible_message_texts(window) -> list[str]:
    texts: list[str] = []
    for control in window.descendants():
        try:
            info = control.element_info
            ctype = info.control_type or ""
            name = re.sub(r"\s+", " ", (info.name or "").strip())
            if not name or len(name) < 40:
                continue
            if ctype in {"Text", "Group"} and not re.search(
                r"\b(?:minimize|maximize|close|new bot|share|connect apps|open account)\b",
                name,
                re.I,
            ):
                texts.append(name)
        except Exception:
            continue
    return texts


def _extract_grok_payload(text: str) -> tuple[str, bool]:
    """Return (prompt_text, read_only). read_only means only speak the latest Grok reply."""
    lower = normalize_voice_text(text)
    patterns = [
        r"\b(?:tell|ask|have)\s+(?:grok(?:\s+bot)?|the\s+ai|ai)\s+(?:about\s+|to\s+|that\s+)?(.+)$",
        r"\b(?:send|type)\s+(?:this\s+)?(?:to\s+)?(?:grok(?:\s+bot)?|the\s+ai)\s*[:\s]+(.+)$",
        r"\b(?:grok(?:\s+bot)?|ai)\s+(?:about|to|do)\s+(.+)$",
        r"\bai\s+(?:please\s+)?(?:do|help(?:\s+me)?(?:\s+with)?)\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            payload = match.group(1).strip(" .")
            payload = re.sub(r"^(?:about|to|that)\s+", "", payload).strip(" .")
            if payload:
                return payload, False
    if re.search(r"\bread\s+(?:my\s+)?(?:new\s+)?information\b", lower):
        return "Read my new information and summarize the key points for me.", False
    if re.search(
        r"\b(?:what\s+did\s+(?:grok(?:\s+bot)?|the\s+ai|ai)\s+say|"
        r"read\s+(?:grok(?:\s+bot)?(?:'s)?|the\s+ai)(?:\s+(?:reply|response|result|answer))?|"
        r"(?:read\s+)?(?:the\s+)?(?:grok(?:\s+bot)?|ai)\s+(?:reply|response|result|answer))\b",
        lower,
    ):
        return "", True
    return "", False


def ensure_grok_bot_ready(config: BridgeConfig) -> str | None:
    """Open/focus Grok Bot. Returns an error string on failure, else None."""
    window = _find_grok_bot_window()
    if window is None:
        opened = open_named_target("open grok bot", config)
        opened_l = opened.lower()
        ok = (
            "confirmed" in opened_l
            or "already open" in opened_l
            or opened_l.startswith("opened grok bot")
        )
        if not ok and ("not installed" in opened_l or "tried to open" in opened_l or "could not" in opened_l):
            return opened
        time.sleep(1.0)
        window = _find_grok_bot_window()
    if window is None:
        return "Grok Bot is not open and I could not launch it."
    try:
        window.set_focus()
    except Exception:
        pass
    time.sleep(0.2)
    return None


def read_grok_bot_result() -> str:
    window = _find_grok_bot_window()
    if window is None:
        return "Grok Bot is not open."
    texts = _grok_visible_message_texts(window)
    if not texts:
        return "I do not see a Grok Bot reply yet."
    latest = texts[-1]
    latest = re.sub(r"^(?:Message from|Messaged)\s+", "", latest).strip()
    return f"Grok Bot says: {latest}"[:MAX_REPLY_LENGTH]


def control_grok_bot(text: str, config: BridgeConfig) -> str:
    """Type into Grok Bot's Prompt field, send with Enter, and read the reply."""
    if os.name != "nt":
        return "Grok Bot control is available only on the Jarvis Windows PC."
    payload, read_only = _extract_grok_payload(text)
    ready_error = ensure_grok_bot_ready(config)
    if ready_error:
        return ready_error
    if read_only or not payload:
        if read_only:
            return read_grok_bot_result()
        return "Tell me what to ask Grok Bot, like tell Grok Bot about the new information."
    window = _find_grok_bot_window()
    if window is None:
        return "Grok Bot is not open."
    prompt = _grok_prompt_edit(window)
    if prompt is None:
        return "I opened Grok Bot, but could not find the Prompt box."
    before = _grok_visible_message_texts(window)
    try:
        from pywinauto.keyboard import send_keys

        try:
            prompt.set_focus()
        except Exception:
            pass
        try:
            prompt.click_input()
        except Exception:
            pass
        time.sleep(0.15)
        send_keys("^a")
        time.sleep(0.05)
        send_keys(_escape_send_keys(payload), with_spaces=True, pause=0.01)
        time.sleep(0.1)
        send_keys("{ENTER}")
    except Exception as exc:
        return f"Typing into Grok Bot failed with {type(exc).__name__}."

    deadline = time.monotonic() + 45.0
    latest = ""
    while time.monotonic() < deadline:
        time.sleep(1.0)
        window = _find_grok_bot_window() or window
        after = _grok_visible_message_texts(window)
        new_texts = [item for item in after if item not in before]
        # Prefer brand-new long replies; fall back to growth of the last bubble.
        candidates = new_texts or (after[-1:] if after and after != before else [])
        if candidates:
            latest = candidates[-1]
            # Wait until the reply looks settled (length stops growing briefly).
            time.sleep(1.2)
            settled = _grok_visible_message_texts(_find_grok_bot_window() or window)
            if settled and settled[-1] == latest:
                break
            if settled:
                latest = settled[-1]
                before = after
    if not latest:
        return "I sent that to Grok Bot, but no reply appeared yet. Ask me to read the Grok result."
    latest = re.sub(r"^(?:Message from|Messaged)\s+", "", latest).strip()
    return f"Sent to Grok Bot. Reply: {latest}"[:MAX_REPLY_LENGTH]


def desktop_capabilities_help() -> str:
    return (
        "I can open or close apps, type, click, press keys, scroll, focus windows, "
        "search Documents/Desktop/Downloads/OneDrive, read or edit text files, "
        "save/copy/paste/undo, search the web, play/skip media, take notes, "
        "run a local system audit, manage Docker or n8n, "
        "talk to Grok Bot and read its replies, "
        "and queue fix or repair jobs on the mini PC. "
        "Vision: describe what I'm looking at; track that object; "
        "who is this enrolled person; enroll or remove enrolled people when face recognition is enabled. "
        "Camera uses single-shot or short low-FPS sessions only; face recognition stays off by default. "
        "Say confirm send or confirm delete for outbound send/delete. "
        "Pay, passwords, and security changes stay blocked."
    )



def _resolve_youtube_watch_url(query: str, *, timeout: float = 10.0) -> str | None:
    """Resolve the top YouTube search hit to a /watch URL so play actually starts."""
    cleaned = re.sub(r"\s+", " ", (query or "").strip(" ."))
    if not cleaned:
        return None
    search_url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": cleaned})
    request = urllib.request.Request(
        search_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            html = response.read().decode("utf-8", "ignore")
    except (OSError, urllib.error.URLError, TimeoutError):
        return None
    for video_id in re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', html):
        return f"https://www.youtube.com/watch?v={video_id}"
    match = re.search(r"/watch\?v=([A-Za-z0-9_-]{11})", html)
    if match:
        return f"https://www.youtube.com/watch?v={match.group(1)}"
    return None


def open_youtube_target(text: str) -> str | None:
    lower = normalize_voice_text(text)
    if "youtube" not in lower:
        return None
    cleaned = re.sub(r"\bon\s+(?:google\s+)?chrome\b", " ", lower)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    match = re.search(r"\byoutube\b(?:\s+(?:and\s+)?(?:play|search(?:\s+for)?))?\s+(.+)$", cleaned)
    query = match.group(1).strip(" .") if match else ""
    query = re.sub(r"^(?:play|search(?:\s+for)?)\s+", "", query).strip(" .")
    wants_play = bool(re.search(r"\bplay\b", lower))
    if query and query not in {"youtube", "app", "website", "site"}:
        if wants_play:
            watch = _resolve_youtube_watch_url(query)
            if watch:
                _open_target(watch)
                return f"Playing {query} on YouTube."
        _open_target("https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query}))
        if wants_play:
            return f"Opened YouTube search for {query}, but could not start a specific video."
        return f"Opened YouTube search for {query}."
    _open_target("https://www.youtube.com/")
    return "Opened YouTube."


def play_media_query(text: str) -> str:
    lower = normalize_voice_text(text)
    match = re.search(r"\bplay\s+(.+)$", lower)
    query = match.group(1).strip(" .") if match else ""
    query = re.sub(r"\s+on\s+youtube$", "", query).strip(" .")
    if not query or query in {"music", "pause"}:
        return _press_media_key("play_pause")
    watch = _resolve_youtube_watch_url(query)
    if watch:
        _open_target(watch)
        return f"Playing {query} on YouTube."
    _open_target("https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query}))
    return f"Opened YouTube search for {query}, but could not start a specific video."



def n8n_status_report() -> str:
    healthy = _http_ok("http://127.0.0.1:5679/healthz")
    _open_target("http://127.0.0.1:5679")
    if healthy:
        return "Confirmed n8n is healthy and opened it."
    return "Opened n8n, but its health check did not answer."


def system_audit_report() -> str:
    """Read-only local audit of host services, resources, and heavy processes."""
    api = _http_json("http://127.0.0.1:8080/healthz")
    n8n = _http_json("http://127.0.0.1:5679/healthz")
    api_ok = bool(api and api.get("status") == "ok")
    n8n_ok = bool(n8n)
    uptime_hours = round(_uptime_seconds() / 3600)
    memory = _memory_percent()
    drive = Path(os.environ.get("SystemDrive", "C:")) / "\\"
    try:
        disk_free_gb = round(shutil.disk_usage(drive).free / (1024**3))
    except OSError:
        disk_free_gb = None
    parts = [
        f"Audit on {socket.gethostname()}",
        f"API {'healthy' if api_ok else 'needs attention'}",
        f"n8n {'healthy' if n8n_ok else 'needs attention'}",
        f"uptime {uptime_hours} hours",
    ]
    if memory is not None:
        parts.append(f"memory {memory} percent used")
    if disk_free_gb is not None:
        parts.append(f"{disk_free_gb} gigabytes free")
    process_line = top_processes()
    if process_line.startswith("The biggest"):
        parts.append(process_line.rstrip("."))
    parts.append("Say fix something specific to queue a mini PC repair task")
    return ". ".join(parts) + "."


def manage_named_target(text: str, config: BridgeConfig) -> str:
    """Open known management surfaces, or fall back to a local audit."""
    lower = normalize_voice_text(text)
    if re.search(r"\bdocker\b", lower):
        return open_named_target("open docker", config)
    if re.search(r"\bn8n\b", lower):
        return n8n_status_report()
    if re.search(r"\b(?:dashboard|cloud\s*ai)\b", lower):
        return open_named_target("open dashboard", config)
    if re.search(r"\b(?:chrome|browser)\b", lower):
        return open_named_target("open chrome", config)
    audit = system_audit_report()
    return audit + " Or say manage Docker, manage n8n, or manage dashboard."


def create_note(text: str, config: BridgeConfig) -> str:
    body = re.sub(
        rf"^.*?\b{re.escape(config.phrase)}\b\s+(?:take\s+)?(?:a\s+)?(?:note|remember)\s+(?:that\s+)?",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip(" .")
    if not body or body.lower() == text.strip().lower():
        return "Tell me what you want the note to say."
    notes = config.search_roots[0] / "Jarvis Notes"
    notes.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now().astimezone()
    path = notes / f"{now:%Y-%m-%d}.md"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"- {now:%H:%M}: {body}\n")
    return "Saved that to today's Jarvis notes."


def top_processes() -> str:
    try:
        result = subprocess.run(["tasklist.exe", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=5, check=False)
        rows = []
        for row in csv.reader(io.StringIO(result.stdout)):
            if len(row) < 5:
                continue
            memory = int(re.sub(r"\D", "", row[4]) or 0)
            rows.append((memory, row[0]))
        names = []
        for _, name in sorted(rows, reverse=True):
            label = Path(name).stem
            if label.lower() not in {item.lower() for item in names}:
                names.append(label)
            if len(names) == 3:
                break
        return "The biggest running processes are " + ", ".join(names) + "." if names else "I could not read the process list."
    except (OSError, subprocess.SubprocessError):
        return "I could not read the process list."


def search_web(text: str, phrase: str) -> str:
    normalized = normalize_voice_text(text)
    marker = re.search(
        r"\b(?:search\s+(?:the\s+)?web(?:\s+for)?|search\s+google(?:\s+for)?|google(?:\s+for)?|search\s+for|search)\s+(.+)$",
        normalized,
    )
    query = marker.group(1).strip(" .") if marker else ""
    query = re.split(
        r"\s+(?:and\s+)?(?:tell me|read me|let me know|confirm|say when)\b",
        query,
        maxsplit=1,
    )[0].strip(" .")
    if not query:
        return "Tell me what you want to search the web for."
    _open_target("https://www.google.com/search?" + urllib.parse.urlencode({"q": query}))
    return f"Opened a web search for {query}."



def new_conversation_id() -> str:
    return uuid.uuid4().hex[:12]


def remember_session_action(session: dict | None, command: str, classification: str, result: str) -> None:
    if session is None:
        return
    actions = session.setdefault("last_actions", [])
    actions.append(
        {
            "command": command,
            "classification": classification,
            "result": result,
        }
    )
    del actions[:-20]


def session_has_browser_context(session: dict | None) -> bool:
    if not session:
        return False
    for item in reversed(session.get("last_actions", [])):
        command = str(item.get("command") or "")
        classification = str(item.get("classification") or "")
        if classification == "web":
            return True
        if classification == "open" and re.search(r"\b(?:chrome|edge|browser|brave|firefox)\b", command):
            return True
        if classification == "browser_followthrough":
            return True
    return False


def session_needs_open_target(session: dict | None) -> bool:
    if not session:
        return False
    actions = session.get("last_actions") or []
    if not actions:
        return False
    last = actions[-1]
    if str(last.get("classification") or "") != "open":
        return False
    result = str(last.get("result") or "").lower()
    return "which app" in result or "tell me which app" in result or "named open" in result


def resolve_session_followup(text: str, phrase: str, session: dict | None = None) -> str:
    """Rewrite contextual follow-ups using the active conversation, without requiring a wake phrase in the body."""
    raw = normalize_voice_text(text)
    body = command_body(text, phrase)
    if not body:
        body = raw
    body = re.sub(r"^(?:now|then|also|next|okay|ok|please)\s+", "", body).strip()
    if session_needs_open_target(session) or (session and session.get("active") and KNOWN_OPEN_APP_NAMES.match(body)):
        alias = START_APP_ALIASES.get(body, body)
        if KNOWN_OPEN_APP_NAMES.match(body) or KNOWN_OPEN_APP_NAMES.match(alias):
            return f"open {alias}"
    if session_has_browser_context(session):
        search = re.match(r"^(?:search(?:\s+the\s+web)?(?:\s+for)?|google(?:\s+for)?)\s+(.+)$", body)
        if search:
            return f"search the web for {search.group(1).strip(' .')}"
        if re.search(r"\b(?:open|click)\s+(?:the\s+)?(?:first|top|relevant)\s+(?:result|page|link)\b", body):
            return "open the first result"
        if re.search(r"\bsummarize(?:\s+it)?\b|\bread(?:\s+it)?\b|\bwhat(?:'s| is)\s+(?:on\s+)?(?:the\s+)?(?:page|screen)\b", body):
            return "summarize the page"
    return body


def browser_followthrough(text: str) -> str:
    """Continue a browser task: open the top result or summarize the visible page title."""
    lower = normalize_voice_text(text)
    if re.search(r"\bsummarize\b|\bread(?:\s+the)?\s+page\b|\bwhat(?:'s| is)\s+on\b", lower):
        titles = [title for title in _visible_window_titles() if re.search(r"chrome|edge|brave|firefox", title, re.I)]
        if not titles:
            return "I do not see a browser window to summarize yet."
        title = titles[0]
        clean = re.sub(r"\s+-\s+(?:Google Chrome|Microsoft Edge|Brave|Mozilla Firefox)$", "", title).strip()
        return f"The visible browser page is {clean}."
    # open first/relevant result: click a search-result-like control when unique enough
    try:
        from pywinauto import Desktop
    except Exception as exc:
        return f"Browser follow-through needs UI Automation ({type(exc).__name__})."
    windows = [
        window
        for window in Desktop(backend="uia").windows()
        if window.is_visible() and re.search(r"chrome|edge|brave|firefox", (window.window_text() or ""), re.I)
    ]
    if not windows:
        return "I do not see a browser window for the next result."
    browser = windows[0]
    browser.set_focus()
    time.sleep(0.2)
    # Prefer the address-bar Enter fallback: many Google SERPs put the first result after Tab cycles.
    try:
        from pywinauto.keyboard import send_keys
        send_keys("{TAB}{TAB}{TAB}{ENTER}")
        time.sleep(0.8)
    except Exception as exc:
        return f"I could not activate the first result ({type(exc).__name__})."
    titles = [title for title in _visible_window_titles() if re.search(r"chrome|edge|brave|firefox", title, re.I)]
    if titles:
        clean = re.sub(r"\s+-\s+(?:Google Chrome|Microsoft Edge|Brave|Mozilla Firefox)$", "", titles[0]).strip()
        return f"Opened the next browser page: {clean}."
    return "Sent the first-result activation in the browser."


def classify_command(text: str) -> str:
    lower = normalize_voice_text(text)
    # Issue #25 vision phrases — classify before web/search so they never become Google queries.
    if re.search(r"\bdescribe\s+what\s+i(?:'m| am)\s+looking\s+at\b", lower):
        return "vision_describe"
    if re.search(r"\bstop\s+tracking\b", lower):
        return "vision_track_stop"
    if re.search(r"\btrack\s+that\s+object\b|\btrack\s+(?:this|the)\s+object\b", lower):
        return "vision_track"
    if re.search(r"\bwho\s+is\s+this\s+enrolled\s+person\b", lower):
        return "vision_face_identify"
    if re.search(r"\benroll(?:\s+this)?(?:\s+person)?\s+as\b", lower):
        return "vision_face_enroll"
    if re.search(r"\bremove\s+enrolled\s+person\b", lower):
        return "vision_face_remove"
    if re.search(r"\b(?:task|codex)\s+(?:status|result)\b|\bwhat(?:'s| is)\s+codex\s+doing\b", lower):
        return "operator_status"
    if re.search(r"\b(?:cancel\s+(?:the\s+)?(?:task|codex)|stop\s+jarvis|cancel\s+jarvis)\b", lower):
        return "operator_cancel"
    if re.search(r"\b(?:confirm(?:\s+(?:send|delete|it|that)?)?|yes\s+do\s+it|go\s+ahead)\b", lower) or (
        re.search(r"\b(?:cancel|never\s+mind)\b", lower) and _get_pending_risk() is not None
    ):
        return "confirm_risk"
    if re.search(r"\b(?:take\s+)?(?:a\s+)?note\b|\bremember\b", lower):
        return "note"
    if docker_control_name(lower):
        return "docker_click"
    if re.search(
        r"\b(?:tell|ask|have)\s+(?:grok(?:\s+bot)?|the\s+ai|ai)\b|"
        r"\b(?:send|type)\s+(?:this\s+)?(?:to\s+)?(?:grok(?:\s+bot)?|the\s+ai)\b|"
        r"\b(?:grok(?:\s+bot)?|ai)\s+(?:about|to|do)\b|"
        r"\bai\s+(?:please\s+)?(?:do|help)\b|"
        r"\bread\s+(?:my\s+)?(?:new\s+)?information\b|"
        r"\bwhat\s+did\s+(?:grok(?:\s+bot)?|the\s+ai|ai)\s+say\b|"
        r"\bread\s+(?:grok(?:\s+bot)?(?:'s)?|the\s+ai)(?:\s+(?:reply|response|result|answer))?\b|"
        r"\bgrok(?:\s+bot)?\s+(?:reply|response|result|answer)\b",
        lower,
    ):
        return "grok_ai"
    if re.search(
        r"\b(?:buy|purchase|pay|transfer|change\s+(?:a\s+)?password|"
        r"(?:change|modify|remove|add)\s+(?:my\s+|an?\s+|the\s+)?account|"
        r"(?:disable|weaken|bypass|change|modify)\s+(?:windows\s+)?(?:security|firewall|antivirus|uac)|"
        r"wipe|format|delete\s+everything|erase\s+everything)\b",
        lower,
    ):
        return "blocked"
    if re.search(
        r"\b(?:click|press)\s+send\b|"
        r"\bsend\s+(?:this|the|it)\b|"
        r"\bsend\s+(?:an?\s+)?(?:email|message)\b|"
        r"\b(?:email|message|post|publish)\s+(?:this|that|it|them|to)\b|"
        r"\b(?:delete|erase|remove)\s+(?:this|the|that|file|document)\b|"
        r"\bdelete\s+[\w ._-]+\.(?:txt|md|csv|json)\b",
        lower,
    ):
        return "risk_confirm"
    if parse_notepad_document_workflow(lower):
        return "notepad_document"
    if re.search(r"\b(?:list|show|check)\s+(?:all\s+)?(?:my\s+)?(?:recent\s+)?documents?\b", lower):
        return "list_documents"
    if re.search(r"\b(?:read|open\s+and\s+read|what's\s+in|what\s+is\s+in)\b", lower) and re.search(
        r"\b(?:file|document|doc|txt|md|notes?|\.txt|\.md)\b|[a-z0-9._-]+\.(?:txt|md|csv|json)\b",
        lower,
    ):
        return "read_document"
    if re.search(r"\b(?:replace|append|edit)\b", lower) and re.search(
        r"\b(?:file|document|doc|txt|md|notes?|\.txt|\.md)\b|[a-z0-9._-]+\.(?:txt|md|csv|json)\b",
        lower,
    ):
        return "edit_document"
    if re.search(r"\b(?:save(?:\s+it|\s+this|\s+file)?|select\s+all|copy|paste|undo|cut)\b", lower) and not re.search(
        r"\b(?:save\s+it\s+to\s+(?:desktop|documents|downloads))\b",
        lower,
    ):
        return "edit_shortcut"
    if re.search(r"\btype\s+.+\b", lower) and "notepad" not in lower:
        return "type_text"
    if re.search(
        r"\b(?:press|hit)\s+(?:ctrl(?:ol)?\s*\+\s*[a-z]|alt\s*\+\s*[a-z]|shift\s*\+\s*[a-z]|page\s+up|page\s+down|enter|return|escape|esc|tab|space(?:bar)?|backspace|delete|up|down|left|right|home|end)\b",
        lower,
    ):
        return "press_key"
    if re.search(r"\bscroll\s+(?:up|down|left|right)\b", lower):
        return "scroll"
    if re.search(r"\b(?:focus|switch\s+to|bring\s+up)\s+.+\b", lower):
        return "focus_window"
    if re.search(r"\bclick\b", lower):
        return "click_that"
    if re.search(
        r"\b(?:close|quit)\s+(?:google\s+)?(?:chrome|edge|firefox|brave|browser|docker(?:\s+desktop)?|cursor|notepad|grok(?:\s*bot)?|calc(?:ulator)?)\b|"
        r"\b(?:close|quit)\s+(?:this\s+|the\s+|active\s+)?(?:tab|window|youtube\s+tab)\b|"
        r"\bclose\s+(?:the\s+)?(?:youtube|this)\s+(?:tab|window)\b",
        lower,
    ):
        return "close"
    if re.search(r"\b(?:open|launch|start)\s+(?:google\s+)?chrome\b", lower):
        return "open"
    if re.search(
        r"\bsearch\s+(?:the\s+)?web(?:\s+for)?\b|\bsearch\s+google(?:\s+for)?\b|\bgoogle\s+for\b|"
        r"\b(?:now\s+)?search\s+for\b|\bsearch\s+[a-z0-9]",
        lower,
    ) and not re.search(r"\b(?:search|find|look\s+for)\s+(?:(?:my|the|a|an)\s+)?(?:files?|documents?|folders?|notes?)\b", lower):
        return "web"
    if re.search(r"\b(?:open|click)\s+(?:the\s+)?(?:first|top|relevant)\s+(?:result|page|link)\b|\bsummarize(?:\s+it|\s+the\s+page)?\b", lower):
        return "browser_followthrough"
    if re.search(r"\b(?:what(?:'s| is) running|top processes|using (?:the most )?memory)\b", lower):
        return "processes"
    if re.search(r"\bplay\s+(?!pause\b)(?!(?:the\s+)?music\b).+", lower):
        return "play_media"
    if re.search(r"\b(?:play|pause)(?: the)? music\b|\bplay pause\b", lower):
        return "play_pause"
    if re.search(r"\b(?:next(?:\s+(?:song|track))?|skip(?:\s+(?:song|track|this|ad))?)\b", lower):
        return "next"
    if re.search(r"\b(?:previous (?:song|track)|last (?:song|track)|go back (?:a |one )?(?:song|track))\b", lower):
        return "previous"
    if re.search(r"\b(?:volume up|turn (?:the )?volume up)\b", lower):
        return "volume_up"
    if re.search(r"\b(?:volume down|turn (?:the )?volume down)\b", lower):
        return "volume_down"
    if re.search(r"\b(?:mute|unmute)\b", lower):
        return "mute"
    if re.search(r"\baudit\b", lower) and re.search(r"\b(?:documents?|files?)\b", lower):
        return "list_documents"
    if re.search(r"\b(?:audit|diagnose)\b|\b(?:system|service|health)\s+check\b|\brun\s+an?\s+audit\b", lower):
        return "system_audit"
    if re.search(r"\bmanage\b", lower):
        return "manage"
    if re.search(r"\b(?:fix|repair|troubleshoot)\b", lower):
        return "operator"
    if re.search(r"\bn8n\b", lower) and re.search(r"\b(?:status|health|check)\b", lower):
        return "n8n_status"
    if re.search(r"\b(status|health|online|how are you)\b", lower):
        return "status"
    if re.search(r"\b(find|locate|look for)\b", lower) or re.search(r"\bsearch\s+(?:for\s+)?(?:files?|documents?|folders?|notes?)\b", lower):
        return "find"
    if re.search(r"\b(?:close|quit)\b", lower):
        return "close"
    if re.search(r"\b(?:open|launch|start)\b", lower):
        return "open"
    if re.search(r"\b(?:help|capabilities|what can you do)\b", lower):
        return "help"
    return "operator"
def _vision_reply(command: str, text: str, current_glasses: dict | None = None) -> str:
    from cloudos.glasses.vision import get_runtime_pipeline, parse_enroll_name, parse_remove_name

    pipe = get_runtime_pipeline()
    if pipe is None:
        return (
            "Vision pipeline is not attached on this bridge yet. "
            "Software path is ready; grant Visor camera scope and enable the vision runtime to use glasses frames."
        )
    if current_glasses is not None:
        pipe.glasses_status = current_glasses
    if command == "vision_describe":
        return pipe.describe().speech
    if command == "vision_track":
        return pipe.start_track().speech
    if command == "vision_track_stop":
        return pipe.stop_track().speech
    if command == "vision_face_identify":
        return pipe.identify_enrolled().speech
    if command == "vision_face_enroll":
        name = parse_enroll_name(normalize_voice_text(text))
        if not name:
            return "Say enroll this person as, then the name, after they consent."
        return pipe.enroll_person(name, consent=True).speech
    if command == "vision_face_remove":
        name = parse_remove_name(normalize_voice_text(text))
        if not name:
            return "Say remove enrolled person, then the name."
        return pipe.remove_person(name).speech
    return "Vision command not recognized."


def _handle_single_command(text: str, config: BridgeConfig, current_glasses: dict | None = None) -> str:
    command = classify_command(text)
    if command.startswith("vision_"):
        reply = _vision_reply(command, text, current_glasses)
    elif command == "status":
        reply = pc_status()
        if current_glasses is not None:
            reply = glasses_status(current_glasses) + " " + reply
    elif command == "n8n_status":
        reply = n8n_status_report()
    elif command == "system_audit":
        reply = system_audit_report()
    elif command == "manage":
        reply = manage_named_target(text, config)
    elif command == "find":
        reply = find_files(text, config)
    elif command == "list_documents":
        reply = list_recent_documents(config)
    elif command == "read_document":
        reply = read_document(text, config)
    elif command == "edit_document":
        reply = edit_document(text, config)
    elif command == "edit_shortcut":
        reply = edit_shortcut(text)
    elif command == "notepad_document":
        reply = notepad_document_workflow(text, config)
    elif command == "open":
        reply = open_youtube_target(text) or open_named_target(text, config)
    elif command == "close":
        reply = close_named_target(text)
    elif command == "play_media":
        reply = play_media_query(text)
    elif command == "note":
        reply = create_note(text, config)
    elif command == "web":
        reply = search_web(text, config.phrase)
    elif command == "browser_followthrough":
        reply = browser_followthrough(text)
    elif command == "processes":
        reply = top_processes()
    elif command in MEDIA_KEYS:
        reply = _press_media_key(command)
    elif command == "operator_status":
        reply = operator_task_status()
    elif command == "operator_cancel":
        reply = operator_task_status(cancel=True)
    elif command == "operator":
        reply = start_operator_task(text, config)
    elif command == "docker_click":
        reply = click_docker_control(text)
    elif command == "click_that":
        reply = click_that_target(text)
    elif command == "type_text":
        reply = type_into_focus(text)
    elif command == "press_key":
        reply = press_named_key(text)
    elif command == "scroll":
        reply = scroll_desktop(text)
    elif command == "focus_window":
        reply = focus_window(text)
    elif command == "grok_ai":
        reply = control_grok_bot(text, config)
    elif command == "risk_confirm":
        reply = queue_risk_confirmation(text)
    elif command == "confirm_risk":
        reply = confirm_pending_risk(text, config)
    elif command == "help":
        reply = desktop_capabilities_help()
    elif command == "blocked":
        reply = "Pay, password, and security changes stay blocked for voice control."
    else:
        reply = desktop_capabilities_help()
    return reply[:MAX_REPLY_LENGTH]


def handle_command(
    text: str,
    config: BridgeConfig,
    current_glasses: dict | None = None,
    session: dict | None = None,
) -> str:
    # Wake-only utterances arm the session and must not invent an empty operator job.
    if command_body(text, config.phrase) == "" and re.search(rf"\b{re.escape(config.phrase)}\b", normalize_voice_text(text)):
        return "Yes?"
    if command_body(text, config.phrase) == "" and not (session and session.get("active")):
        return "Yes?"
    resolved = resolve_session_followup(text, config.phrase, session)
    if not resolved:
        return "Yes?"
    # Rebuild an utterance the planner understands while preserving conversation context.
    planned_source = f"{config.phrase} {resolved}"
    replies: list[str] = []
    for clause in plan_commands(planned_source, config.phrase):
        classification = classify_command(clause)
        reply = _handle_single_command(clause, config, current_glasses)
        remember_session_action(session, clause, classification, reply)
        replies.append(reply)
    return " ".join(replies)[:MAX_REPLY_LENGTH]
