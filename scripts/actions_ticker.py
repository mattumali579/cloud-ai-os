#!/usr/bin/env python3
"""Reliable clock for this repo's GitHub Actions.

GitHub's own `schedule:` trigger is badly unreliable here (some crons never
fire). This script runs inside the "Ticker" workflow, wakes every 30 seconds,
and starts each target workflow through the workflow_dispatch API when its
slot comes due. Dispatches made with the built-in GITHUB_TOKEN always create
runs (documented exception), so no extra credential is needed.

A slot counts as done if ANY run of that workflow (scheduled, manual or ours)
was created at or after the slot time, so hand-offs between ticker runs and
late GitHub schedule firings never cause a double start.

PUBLIC repo: logs only workflow file names and times.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
CHICAGO = ZoneInfo("America/Chicago")


def slot_every_10_min(now: datetime) -> datetime:
    return now.replace(minute=now.minute - now.minute % 10, second=0, microsecond=0)


def slot_lead_engine(now: datetime) -> datetime:
    # '17 */3 * * *' in UTC
    t = now.replace(minute=17, second=0, microsecond=0)
    while t > now or t.hour % 3:
        t -= timedelta(hours=1)
    return t


def slot_email_alerts(now: datetime) -> datetime:
    # '0 7,9,11,13,15,17,19,21,23 * * *' America/Chicago
    local = now.astimezone(CHICAGO).replace(minute=0, second=0, microsecond=0)
    while local.hour not in (7, 9, 11, 13, 15, 17, 19, 21, 23) or local > now:
        local = (local - timedelta(hours=1)).astimezone(CHICAGO)
    return local.astimezone(UTC)


# (workflow file, dispatch inputs, slot function, catch-up grace)
TARGETS = [
    ("outreach-replies.yml", {"mode": "poll"}, slot_every_10_min, timedelta(minutes=10)),
    ("email-alerts.yml", {"mode": "normal"}, slot_email_alerts, timedelta(minutes=60)),
    ("lead-engine.yml", {"mode": "cycle"}, slot_lead_engine, timedelta(minutes=60)),
]


class GitHub:
    def __init__(self, repo: str, token: str, ref: str):
        self.base = f"https://api.github.com/repos/{repo}"
        self.token = token
        self.ref = ref

    def _call(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            self.base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "actions-ticker",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)

    def ran_since(self, workflow: str, since: datetime) -> bool:
        stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        _, data = self._call("GET", f"/actions/workflows/{workflow}/runs?created=%3E%3D{stamp}&per_page=1")
        return bool(data and data.get("total_count"))

    def dispatch(self, workflow: str, inputs: dict) -> int:
        status, _ = self._call("POST", f"/actions/workflows/{workflow}/dispatches",
                               {"ref": self.ref, "inputs": inputs})
        return status


def log(msg: str) -> None:
    print(f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ} {msg}", flush=True)


def tick(gh: GitHub, done: dict, now: datetime) -> None:
    for workflow, inputs, slot_fn, grace in TARGETS:
        slot = slot_fn(now)
        if done.get(workflow) == slot:
            continue
        try:
            if gh.ran_since(workflow, slot):
                log(f"{workflow}: slot {slot:%H:%M}Z already has a run")
            elif now - slot > grace:
                log(f"{workflow}: slot {slot:%H:%M}Z too old to catch up, skipping")
            else:
                status = gh.dispatch(workflow, inputs)
                log(f"{workflow}: slot {slot:%H:%M}Z dispatched (HTTP {status})")
            done[workflow] = slot
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log(f"{workflow}: API error {getattr(exc, 'code', '')} - will retry")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=50, help="how long to keep ticking")
    ap.add_argument("--interval", type=float, default=30)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        log("BLOCKED: GITHUB_TOKEN / GITHUB_REPOSITORY not set")
        return 1
    gh = GitHub(repo, token, os.environ.get("TICKER_REF", "master"))

    done: dict = {}
    deadline = time.monotonic() + args.minutes * 60
    while True:
        tick(gh, done, datetime.now(UTC))
        if args.once or time.monotonic() + args.interval > deadline:
            break
        time.sleep(args.interval)
    log("ticker window finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
