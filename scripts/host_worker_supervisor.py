"""Supervise the existing host worker as a single Windows background process.

Production path is Docker Compose (db + agent-api + n8n) plus one host worker.
This supervisor waits until the compose API is healthy, then runs
``scripts/host_worker_boot.py`` in-process. It does not start a second API.

A PID lock prevents duplicate supervisors. Unexpected worker exit is logged and
retried with bounded backoff so a boot-order race cannot permanently kill work.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

REPO = Path(__file__).resolve().parent.parent
LOCK_PATH = REPO / "logs" / "host_worker.pid"
LOG_PATH = REPO / "logs" / "host_worker.log"
MAX_BACKOFF_SECONDS = 15
WAIT_BACKOFF_CAP = 15

log = logging.getLogger("host_worker_supervisor")

_lock_fh = None


def acquire_lock(path: Path = LOCK_PATH) -> bool:
    """True if this process now owns an exclusive lock file kept open for the lifetime."""
    global _lock_fh
    path.parent.mkdir(exist_ok=True)
    fh = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()).encode("ascii"))
    fh.flush()
    _lock_fh = fh
    return True


def api_health(port: int, timeout: float = 2.0) -> dict | None:
    """Return parsed /healthz JSON, or None if unreachable/invalid."""
    try:
        with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as response:  # noqa: S310
            if response.status != 200:
                return None
            return json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None


def wait_for_api(port: int, stop_after: float | None = None) -> bool:
    """Block until /healthz reports db true. Returns False only if stop_after elapses."""
    deadline = None if stop_after is None else time.monotonic() + stop_after
    delay = 1.0
    while True:
        payload = api_health(port)
        if payload and payload.get("status") == "ok" and payload.get("db") is True:
            log.info("compose API healthy on port %s (db=true)", port)
            return True
        if deadline is not None and time.monotonic() >= deadline:
            log.warning("compose API not healthy on port %s before timeout", port)
            return False
        log.info("waiting for compose API on port %s (last=%s)", port, payload)
        time.sleep(delay)
        delay = min(delay * 2, WAIT_BACKOFF_CAP)


def configure_logging() -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        LOG_PATH, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[handler],
        force=True,
    )


def _boot_port() -> int:
    os.environ.setdefault("INSTANCE_NAME", "host-worker")
    src = REPO / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from cloudos.config import get_settings

    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()
    return get_settings().agent_api_port


def run_worker_once() -> None:
    boot_path = REPO / "scripts" / "host_worker_boot.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location("host_worker_boot", boot_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {boot_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()


def main() -> int:
    os.chdir(REPO)
    configure_logging()
    if not acquire_lock():
        log.warning("another host worker supervisor is already running — exiting")
        return 0

    log.info("host worker supervisor starting from %s pid=%s", REPO, os.getpid())
    backoff = 2.0
    while True:
        try:
            port = _boot_port()
            wait_for_api(port)
            log.info("launching existing host_worker_boot")
            run_worker_once()
            log.warning("host_worker_boot returned; restarting in %.1fs", backoff)
        except KeyboardInterrupt:
            log.info("supervisor interrupted — stopping")
            return 0
        except Exception:
            log.exception("host worker crashed; restarting in %.1fs", backoff)
        time.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
