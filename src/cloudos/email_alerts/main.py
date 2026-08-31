"""Entry point: mailbox -> importance filter -> Discord.

Runs in GitHub Actions. Nothing here touches the laptop, a tunnel, or a local
worker; the only inputs are environment secrets and the only output is a
Discord message plus a committed dedup file.

Exit codes
  0  ran to completion (zero important mail is a success, not a failure)
  1  could not do its job (mailbox unreachable, auth rejected, no webhook)

Modes
  (default)     read the mailbox and alert
  --self-test   prove cloud execution + Discord delivery + the filter, with no
                mailbox credential. Used to verify the pipeline before the app
                password exists, and as a liveness check afterwards.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .filter import classify
from .notifier import resolve_destination, send_email_alert, send_plain

STATE_PATH = Path(os.environ.get("EMAIL_ALERTS_STATE", "state/email_alerts_seen.json"))
STATE_KEEP_DAYS = 14
STATE_KEEP_MAX = 800


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("seen"), dict):
            return data
    except Exception:
        pass
    return {"seen": {}}


def save_state(path: Path, state: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STATE_KEEP_DAYS)).isoformat()
    seen = {k: v for k, v in state.get("seen", {}).items() if v >= cutoff}
    if len(seen) > STATE_KEEP_MAX:
        newest = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:STATE_KEEP_MAX]
        seen = dict(newest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"seen": seen, "updated_at": datetime.now(timezone.utc).isoformat()},
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def emit_outputs(**values) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def self_test(webhook: str) -> int:
    """Prove the cloud runtime can classify and deliver, with no mailbox."""
    from .mailbox import Message

    sample = Message(
        uid="selftest",
        message_id="<selftest@cloudos>",
        subject="MATH 1550: Homework 5 due Friday 11:59pm",
        sender="professor@lsu.edu",
        received=datetime.now(timezone.utc),
        snippet=("SELF-TEST - this is not a real email. Homework 5 covering related "
                 "rates is due Friday at 11:59pm on Moodle."),
        headers={},
    )
    result = classify(sample.subject, sample.snippet, sample.sender,
                      sample.headers, owner_addresses=())
    print(f"filter verdict: important={result.important} score={result.score} "
          f"categories={result.categories}")
    if not result.important:
        print("::error::self-test message failed the importance filter")
        return 1
    delivered = send_email_alert(webhook, sample, result)
    print(f"discord delivery: {'ok' if delivered else 'FAILED'}")
    emit_outputs(checked=1, sent=int(delivered), failed=int(not delivered))
    return 0 if delivered else 1


def run() -> int:
    parser = argparse.ArgumentParser(prog="email-alerts")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--since-hours", type=int,
                        default=int(os.environ.get("EMAIL_SINCE_HOURS", "6")))
    parser.add_argument("--dry-run", action="store_true",
                        help="classify and print, deliver nothing")
    args = parser.parse_args()

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    destination, _headers, kind = resolve_destination(webhook)
    if not destination:
        print("::error::no Discord destination. Set CLOUDOS_NOTIFY_URL + "
              "CLOUDOS_NOTIFY_TOKEN (preferred) or DISCORD_WEBHOOK_URL.")
        return 1
    print(f"discord path: {kind}")

    if args.self_test:
        return self_test(webhook)

    host = os.environ.get("EMAIL_IMAP_HOST", "imap.gmail.com").strip()
    user = os.environ.get("EMAIL_ADDRESS", "").strip()
    password = os.environ.get("EMAIL_APP_PASSWORD", "").strip()
    if not user or not password:
        print("::error::EMAIL_ADDRESS and EMAIL_APP_PASSWORD must both be set. "
              "Add them as repository secrets; see docs/EMAIL_ALERTS.md.")
        return 1

    owners = {user.lower()}
    extra = os.environ.get("EMAIL_OWNER_ALIASES", "")
    owners.update(a.strip().lower() for a in extra.split(",") if a.strip())

    from .mailbox import fetch_recent

    try:
        messages = fetch_recent(host, user, password, since_hours=args.since_hours)
    except Exception as exc:
        # Auth and connection problems must be loud: a mailbox that cannot be
        # read looks exactly like a quiet inbox unless it is reported.
        print(f"::error::could not read the mailbox ({type(exc).__name__}). "
              f"Check EMAIL_IMAP_HOST/EMAIL_ADDRESS/EMAIL_APP_PASSWORD and that "
              f"IMAP is enabled for the account.")
        emit_outputs(checked=0, sent=0, failed=1)
        return 1

    state = load_state(STATE_PATH)
    seen = state.setdefault("seen", {})
    now_iso = datetime.now(timezone.utc).isoformat()

    checked = len(messages)
    sent = 0
    failed = 0
    skipped_dupe = 0

    for message in messages:
        key = message.message_id or message.uid
        if key in seen:
            skipped_dupe += 1
            continue
        result = classify(message.subject, message.snippet, message.sender,
                          message.headers, owner_addresses=owners)
        if not result.important:
            continue
        print(f"IMPORTANT [{result.label} score={result.score}] {message.subject!r}")
        if args.dry_run:
            sent += 1
            continue
        if send_email_alert(webhook, message, result):
            sent += 1
            seen[key] = now_iso
        else:
            failed += 1

    print(f"checked={checked} alerted={sent} failed={failed} "
          f"already_seen={skipped_dupe}")

    if not args.dry_run:
        save_state(STATE_PATH, state)
    emit_outputs(checked=checked, sent=sent, failed=failed)
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
