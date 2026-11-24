"""Uptime, auth-denial and TLS-expiry checks for Matt's public endpoints.

Stdlib only. Exits 1 when any check fails so the GitHub Actions run fails and
GitHub's built-in failed-run notification reaches the repo owner. No secrets
are used or printed; every check targets a public URL Matt owns.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

FUNNEL = "https://desktop-bu7o72u.tail4d94c1.ts.net"
# (name, url, expected HTTP status, substring the body must contain or None)
DEFAULT_HTTP_CHECKS = [
    ("muse-gateway uptime", f"{FUNNEL}/healthz", 200, '"status":"ok"'),
    ("muse-gateway rejects anonymous status", f"{FUNNEL}/v1/n8n/status", 401, None),
    ("no n8n admin API behind funnel", f"{FUNNEL}/rest/workflows", 404, None),
    ("no n8n editor behind funnel", f"{FUNNEL}/signin", 404, None),
    ("pages: cloud-ai-os", "https://mattumali579.github.io/cloud-ai-os/", 200, None),
    ("pages: missed-call demo", "https://mattumali579.github.io/office-agent-demo/missed-call/", 200, None),
]
DEFAULT_TLS_HOSTS = ["desktop-bu7o72u.tail4d94c1.ts.net", "mattumali579.github.io"]
MIN_CERT_DAYS = 14


def http_check(name: str, url: str, expect: int, contains: str | None, attempts: int = 3) -> tuple[bool, str]:
    last = ""
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, headers={"User-Agent": "cloudos-site-monitor/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed public URLs
                status, body = resp.status, resp.read(65536).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, body = exc.code, ""
        except Exception as exc:  # timeout, DNS, TLS, connection refused
            status, body = None, ""
            last = f"{type(exc).__name__}"
        if status == expect and (contains is None or contains in body):
            return True, f"{status}"
        last = last if status is None else f"got {status}" + ("" if contains is None or contains in body else ", body check failed")
        if attempt < attempts:
            time.sleep(5 * attempt)
    return False, f"expected {expect}, {last}"


def cert_days_left(host: str) -> float:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=15) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            not_after = tls.getpeercert()["notAfter"]
    expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), tz=timezone.utc)
    return (expires - datetime.now(timezone.utc)).total_seconds() / 86400


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checks-json", help="override HTTP checks (self-test)")
    ap.add_argument("--tls-hosts", nargs="*", default=None)
    args = ap.parse_args()
    checks = json.loads(args.checks_json) if args.checks_json else DEFAULT_HTTP_CHECKS
    tls_hosts = DEFAULT_TLS_HOSTS if args.tls_hosts is None else args.tls_hosts

    rows, failed = [], 0
    for name, url, expect, contains in checks:
        ok, detail = http_check(name, url, expect, contains)
        failed += not ok
        rows.append(("PASS" if ok else "FAIL", name, detail))
    for host in tls_hosts:
        try:
            days = cert_days_left(host)
            ok = days >= MIN_CERT_DAYS
            detail = f"{days:.0f} days left"
        except Exception as exc:
            ok, detail = False, f"TLS error {type(exc).__name__}"
        failed += not ok
        rows.append(("PASS" if ok else "FAIL", f"TLS cert {host}", detail))

    lines = ["| result | check | detail |", "|---|---|---|"] + [f"| {r} | {n} | {d} |" for r, n, d in rows]
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### Site monitor\n\n" + "\n".join(lines) + "\n")
    for r, n, d in rows:
        if r == "FAIL":
            print(f"::error title=site monitor::{n}: {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
