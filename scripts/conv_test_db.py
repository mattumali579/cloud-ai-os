"""Start the THROWAWAY local Postgres the real-database tests need, then run them.

tests/test_conversations.py and tests/test_outreach_sender.py drop and rebuild the whole
`public` schema, so CONVERSATIONS_TEST_DATABASE_URL must only ever point at this local
container - never Supabase, never anything from .env. This script hard-codes localhost.

    python scripts/conv_test_db.py                      start the container, print the variable to set
    python scripts/conv_test_db.py tests/test_conversations.py -q
                                                        start it, then run pytest with the variable set
    python scripts/conv_test_db.py --stop               stop the container (data is disposable)

Needs Docker Desktop running. Same image and credentials as .github/workflows/outreach-tests.yml.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

NAME = "br-conv-test"
PORT = 55432
URL = f"postgresql://postgres:test@localhost:{PORT}/brtest"


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def start() -> None:
    if docker("info").returncode != 0:
        sys.exit("Docker is not running. Start Docker Desktop, wait for the engine, and run this again.")
    if docker("start", NAME).returncode != 0:      # no such container yet: create it, local-only port
        made = docker("run", "-d", "--name", NAME, "-e", "POSTGRES_PASSWORD=test", "-e", "POSTGRES_DB=brtest",
                      "-p", f"127.0.0.1:{PORT}:5432", "postgres:17")
        if made.returncode != 0:
            sys.exit(f"could not create {NAME}: {made.stderr.strip()}")
    for _ in range(60):
        # a real query over TCP: pg_isready alone answers during first-boot init, before brtest exists
        if docker("exec", NAME, "psql", "-h", "127.0.0.1", "-U", "postgres", "-d", "brtest", "-Atc",
                  "select 1").stdout.strip() == "1":
            return
        time.sleep(1)
    sys.exit(f"{NAME} did not become ready in 60s: {docker('logs', '--tail', '5', NAME).stderr.strip()}")


def main(argv: list[str]) -> int:
    if argv == ["--stop"]:
        return docker("stop", NAME).returncode
    start()
    print(f"test database host: localhost:{PORT} (container {NAME}, throwaway)")
    if not argv:
        print(f"CONVERSATIONS_TEST_DATABASE_URL={URL}")
        return 0
    return subprocess.run([sys.executable, "-m", "pytest", *argv], check=False,
                          env={**os.environ, "CONVERSATIONS_TEST_DATABASE_URL": URL}).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
