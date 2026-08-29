"""Windowless launcher for the Cloud AI OS host worker.

Run with pythonw.exe so no console window ever appears at logon. The worker must
run on the HOST (not in Docker) because ``task.run`` shells out to the Claude Code
CLI, whose subscription credentials live in the Windows user profile.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ENV = {
    "DATABASE_URL": "postgresql://cloudos:cloudos@localhost:5432/cloudos",
    "INSTANCE_NAME": "host-worker",
    "WORKER_POLL_INTERVAL_SECONDS": "3",
    "WORKER_CONCURRENCY": "1",
    "TASK_WORKSPACE_ROOT": str(Path.home() / "AI-Second-Brain" / "Brain_Vault" / "Task_Runs"),
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
}

# Billing guard: a metered credential inherited from the user environment would
# silently switch the CLI off subscription auth and onto paid API billing.
METERED = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


def main() -> None:
    for key, value in ENV.items():
        os.environ.setdefault(key, value)
    for key in METERED:
        os.environ.pop(key, None)

    logs = REPO / "logs"
    logs.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(logs / "host_worker.log", encoding="utf-8")],
    )

    src = REPO / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from cloudos.worker.loop import main as worker_main

    logging.getLogger("host_worker_boot").info("host worker booting from %s", REPO)
    worker_main()


if __name__ == "__main__":
    main()
