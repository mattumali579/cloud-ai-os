#!/usr/bin/env python
"""Apply db/migrations/*.sql to the database in DATABASE_URL.

Usage (from repo root):
    python scripts/apply_migrations.py            # apply pending migrations
    python scripts/apply_migrations.py --dry-run  # list pending versions only

Exit codes: 0 success; 2 configuration error (e.g. DATABASE_URL unset);
1 any other failure (connection refused, SQL error, ...).

For Supabase, point DATABASE_URL at a SESSION-mode connection (port 5432);
the migration runner holds a session-level advisory lock, which the
transaction-mode pooler (port 6543) does not support. See docs/SUPABASE.md.
"""
from __future__ import annotations

import pathlib
import sys

# Bootstrap: make src/ importable when running straight from a checkout.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import argparse

from cloudos import db
from cloudos.contracts import CloudOSError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply db/migrations/*.sql in filename order (idempotent via schema_migrations)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list pending migration versions without applying them",
    )
    args = parser.parse_args(argv)

    try:
        if args.dry_run:
            pending = db.pending_migrations()
            if pending:
                print("pending:", ", ".join(pending))
            else:
                print("pending: none (schema is up to date)")
            return 0

        applied = db.migrate()
        if applied:
            print("applied:", ", ".join(applied))
        else:
            print("applied: none (schema is up to date)")
        return 0
    except CloudOSError as exc:
        print(f"error: {exc.code.value}: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:  # connection/SQL failures -> nonzero, message only
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
