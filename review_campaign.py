#!/usr/bin/env python
"""Operate the Google Reviews / Maps campaign without bypassing the existing sender."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from cloudos import db  # noqa: E402
from cloudos.outreach import review_campaign  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("qualify")
    e = sub.add_parser("export"); e.add_argument("--out", required=True)
    i = sub.add_parser("import-copy"); i.add_argument("path")
    q = sub.add_parser("queue"); q.add_argument("--limit", type=int)
    sub.add_parser("status")
    a = p.parse_args(argv)
    db.migrate()
    with db.get_conn() as conn:
        if a.cmd == "qualify":
            out = review_campaign.qualify(conn)
        elif a.cmd == "export":
            leads = review_campaign.selected(conn)
            Path(a.out).write_text(json.dumps(leads, indent=2) + "\n", encoding="utf-8")
            out = {"exported": len(leads), "path": str(Path(a.out).resolve())}
        elif a.cmd == "import-copy":
            out = review_campaign.import_copy(conn, Path(a.path).read_text(encoding="utf-8"))
        elif a.cmd == "queue":
            from cloudos.outreach.sender import postal_address
            out = review_campaign.queue_approved(conn, postal_address=postal_address(), limit=a.limit)
        else:
            out = review_campaign.status(conn)
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

