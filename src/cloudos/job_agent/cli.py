from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from cloudos import db
from cloudos.config import REPO_ROOT, get_settings

from .orchestrator import JobAgent
from .resumes import ResumeGenerator
from .second_brain import ProfileReader
from .tracker import PostgresJobStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job-agent", description="Autonomous fail-safe quick job application agent")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "discover", "apply", "status", "history", "skipped", "resumes", "resume-test", "dry-run", "report"):
        command = sub.add_parser(name)
        if name in {"history", "skipped"}:
            command.add_argument("--limit", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parser().parse_args(argv)
    settings = get_settings()
    if args.command in {"resumes", "resume-test"}:
        profile = ProfileReader(settings.job_profile_path).read()
        generator = ResumeGenerator(profile, REPO_ROOT / "resumes")
        files = generator.generate_all()
        checks = {name: {"path": str(path), "exists": path.is_file(), "bytes": path.stat().st_size if path.is_file() else 0, "pdf": path.read_bytes()[:5] == b"%PDF-" if path.is_file() else False} for name, path in files.items()}
        _print(checks)
        return 0 if all(v["exists"] and v["pdf"] and v["bytes"] > 1000 for v in checks.values()) else 1

    db.migrate()
    with db.get_conn() as conn:
        agent = JobAgent(conn, settings)
        if args.command == "run":
            result = agent.run()
        elif args.command == "discover":
            result = agent.discover_jobs()
            result.pop("qualified", None)
        elif args.command == "apply":
            result = agent.apply_jobs()
            result.pop("top", None)
        elif args.command == "dry-run":
            result = agent.run(dry_run=True)
        elif args.command == "report":
            result = agent.report()
        else:
            store = PostgresJobStore(conn)
            if args.command == "status":
                result = store.daily_counts()
            elif args.command == "history":
                result = store.list(limit=args.limit)
            else:
                result = store.list(status="skipped", limit=args.limit)
        _print(result)
    return 0


def _print(value) -> None:
    print(json.dumps(value, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
