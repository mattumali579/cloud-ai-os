"""Run the local Agent API and Discord adapter as one foreground service."""
from __future__ import annotations

import subprocess
import sys
import time
from urllib.request import urlopen

from cloudos.config import get_settings


def _wait_for_api(url: str, seconds: int = 30) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{url.rstrip('/')}/healthz", timeout=2) as response:  # noqa: S310
                if response.status == 200:
                    return
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    raise RuntimeError("Cloud AI OS API did not become healthy")


def main() -> None:
    settings = get_settings()
    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "cloudos.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(settings.agent_api_port),
        ]
    )
    try:
        _wait_for_api(settings.discord_api_url)
        from cloudos.discord_bot.bot import main as bot_main

        bot_main()
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.kill()


if __name__ == "__main__":
    main()
