"""Windowless, loopback-only launcher for the native Cloud AI OS API."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Keep subscription CLI invocations fail-closed if this task inherits a metered
# credential from the Windows user environment.  The router also scrubs these
# before invoking a provider, but the host boundary must be safe on its own.
METERED = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


def main() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for key in METERED:
        os.environ.pop(key, None)

    src = REPO / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

    logs = REPO / "logs"
    logs.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(logs / "host_api.log", encoding="utf-8")],
    )

    import uvicorn

    from cloudos.config import get_settings

    settings = get_settings()
    logging.getLogger("host_api_boot").info("host API booting on loopback port %s", settings.agent_api_port)
    uvicorn.run("cloudos.api.app:app", host="127.0.0.1", port=settings.agent_api_port, log_config=None)


if __name__ == "__main__":
    main()
