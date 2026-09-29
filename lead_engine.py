#!/usr/bin/env python
"""Fresh lead generator - manual commands. The same engine runs automatically
from .github/workflows/lead-engine.yml and the cloud-ai-os worker ("lead.engine.cycle").

    python lead_engine.py test                      10-company end-to-end test + duplicate re-check
    python lead_engine.py discover --target 100     add 100 NEW outreach-ready companies
    python lead_engine.py status                    inventory + progress toward 4,000
    python lead_engine.py cycle                     what the scheduler runs (refill only if low)
    python lead_engine.py import-history            load every old lead before discovering
    python lead_engine.py handoff [--dry-run]       top up the sender's Airtable tray, sync sends back
    python lead_engine.py reports                   rewrite the status/performance markdown files
    python lead_engine.py reset-source NAME         un-bench a source after fixing it
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from cloudos import db  # noqa: E402
from cloudos.leadgen import reports, store  # noqa: E402
from cloudos.leadgen.engine import LeadEngine, load_config, outreach_ready_rows  # noqa: E402
from cloudos.leadgen.store import Candidate  # noqa: E402

DOCS = ROOT / "docs" / "lead_engine"


def _record_run(mode: str, fn):
    with db.get_conn() as conn:
        run_id = conn.execute("INSERT INTO lead_runs (mode) VALUES (%s) RETURNING run_id", (mode,)).fetchone()["run_id"]
    try:
        result = fn()
    except BaseException as exc:
        with db.get_conn() as conn:
            conn.execute("UPDATE lead_runs SET finished_at = now(), stats = %s::jsonb WHERE run_id = %s",
                         (json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:300]}"}), run_id))
        raise
    with db.get_conn() as conn:
        conn.execute("UPDATE lead_runs SET finished_at = now(), stats = %s::jsonb WHERE run_id = %s",
                     (json.dumps(result, default=str), run_id))
    return result


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_status(engine: LeadEngine, _args) -> int:
    _print(engine.status())
    return 0


def cmd_discover(engine: LeadEngine, args) -> int:
    sources = args.sources.split(",") if args.sources else None
    result = _record_run("discover", lambda: {"stats": engine.discover(args.target, args.max_minutes, sources).__dict__})
    stats = result["stats"]
    _print({k: v for k, v in stats.items() if k != "samples"})
    return 0 if stats["new_outreach_ready"] >= args.target else 3


def cmd_cycle(engine: LeadEngine, args) -> int:
    result = _record_run("cycle", lambda: engine.cycle(args.max_minutes, force=args.force))
    if not args.no_handoff:
        try:
            result["handoff"] = _handoff(dry_run=False)
        except Exception as exc:  # noqa: BLE001 - handoff trouble must not undo discovery
            result["handoff"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    _print(result)
    cmd_reports(engine, args)
    return 0


def _handoff(dry_run: bool) -> dict:
    from cloudos.leadgen.handoff import Airtable, sync_and_handoff
    from cloudos.leadgen.history_import import airtable_env

    env = airtable_env()
    if not all(env.values()):
        return {"skipped": "Airtable credentials not configured here"}
    at = Airtable(env["AIRTABLE_API_KEY"], env["AIRTABLE_BASE_ID"], env["AIRTABLE_TABLE_NAME"])
    with db.get_conn() as conn:
        return sync_and_handoff(conn, at, load_config(), dry_run=dry_run)


def cmd_handoff(_engine, args) -> int:
    _print(_handoff(args.dry_run))
    return 0


def cmd_import(_engine, args) -> int:
    from cloudos.leadgen.history_import import Importer, airtable_env, fetch_airtable

    report: dict = {"sources": {}, "missing": []}
    with db.get_conn() as conn:
        before = store.inventory(conn)
        imp = Importer(conn)
        env = airtable_env()
        if all(env.values()):
            imp.import_airtable(fetch_airtable(env["AIRTABLE_API_KEY"], env["AIRTABLE_BASE_ID"], env["AIRTABLE_TABLE_NAME"]))
            conn.commit()
        else:
            report["missing"].append("airtable: credentials not available")
        for csv_path in [Path.home() / "AI-Second-Brain" / "leads_enriched_roofers_in_baton_rouge__la_20260820_163232.csv",
                         *[Path(p) for p in (args.csv or [])]]:
            if csv_path.exists():
                imp.import_csv(csv_path)
                conn.commit()
            else:
                report["missing"].append(f"csv: {csv_path.name} not found")
        outbox = ROOT / "email_outbox" / "sent"
        if outbox.exists():
            imp.import_outbox(outbox)
            conn.commit()
        user, pw = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASS", "")
        if not (user and pw):
            from dotenv import dotenv_values
            vals = dotenv_values(Path.home() / "AI-Second-Brain" / "leadgen-pipeline" / ".env")
            user, pw = vals.get("SMTP_USER", ""), vals.get("SMTP_PASS", "")
        if user and pw and not args.skip_gmail:
            try:
                imp.import_gmail_sent(user, pw, since=args.gmail_since)
                conn.commit()
            except Exception as exc:  # noqa: BLE001 - one unreachable ledger must not block the rest
                conn.rollback()
                report["missing"].append(f"gmail_sent: {type(exc).__name__}")
        else:
            report["missing"].append("gmail_sent: skipped")
        after = store.inventory(conn)
    report["sources"] = {k: dict(v) for k, v in imp.stats.items()}
    report["before"] = {k: before[k] for k in ("total_companies", "contacts", "domains_indexed")}
    report["after"] = {k: after[k] for k in ("total_companies", "historical_companies", "contacts", "domains_indexed", "contacted")}
    _print(report)
    (ROOT / "state" / "lead_engine").mkdir(parents=True, exist_ok=True)
    (ROOT / "state" / "lead_engine" / "last_import.json").write_text(json.dumps(report, indent=2, default=str))
    return 0


def cmd_reports(engine: LeadEngine, _args) -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    with db.get_conn() as conn:
        (DOCS / "lead_engine_status.md").write_text(reports.status_markdown(conn, engine.cfg), encoding="utf-8")
        (DOCS / "lead_source_performance.md").write_text(reports.performance_markdown(conn), encoding="utf-8")
    print(f"wrote {DOCS / 'lead_engine_status.md'} and {DOCS / 'lead_source_performance.md'}")
    return 0


def cmd_reset_source(_engine, args) -> int:
    with db.get_conn() as conn:
        conn.execute("UPDATE lead_source_health SET consecutive_failures = 0, disabled_until = NULL WHERE source = %s",
                     (args.name,))
    print(f"{args.name} re-enabled")
    return 0


def cmd_test(engine: LeadEngine, args) -> int:
    """Small real end-to-end run, then re-submit the same companies and demand duplicates."""
    sources = args.sources.split(",") if args.sources else ["osm"]
    t0 = time.time()
    stats = engine.discover(args.target, args.max_minutes, sources)
    with db.get_conn() as conn:
        ready = [r for r in outreach_ready_rows(conn, 1000) if r["company_id"] in set(stats.samples)]
        evidence = []
        for r in ready[: args.show]:
            evidence.append({
                "company_id": r["company_id"], "company": r["company_name"], "website": r["website"],
                "industry": r["industry"], "location": f"{r['city']}, {r['state']}", "email": reports.mask_email(r["email"]),
                "email_status": r["email_status"], "email_found_on": r["email_source_url"], "source": r["source"],
                "qualification": r["qualification_level"], "reason": r["qualification_reason"],
            })
        # duplicate proof: feed the exact same companies back in
        rows = conn.execute("SELECT company_name, website, city, state, phone, discovery_source FROM companies "
                            "WHERE company_id = ANY(%s::uuid[])", (stats.samples,)).fetchall()
        again = [Candidate(name=r["company_name"], website=r["website"] or "", city=r["city"] or "", state=r["state"] or "",
                           phone=r["phone"] or "", source="dedupe_test", query="re-submit of test companies") for r in rows]
        from cloudos.leadgen.enrich import Fetcher
        from cloudos.leadgen.engine import RunStats
        second = RunStats()
        fetcher = Fetcher()
        try:
            engine.process_candidates(conn, again, second, fetcher, time.time() + 600)
        finally:
            fetcher.close()
    out = {
        "minutes": round((time.time() - t0) / 60, 1),
        "first_pass": {k: v for k, v in stats.__dict__.items() if k != "samples"},
        "evidence": evidence,
        "second_pass_same_companies": {"submitted": len(again), "duplicates_rejected": second.duplicates,
                                       "inserted_again": second.new_companies},
        "PASS": stats.new_outreach_ready >= min(args.target, 1) and second.new_companies == 0 and second.duplicates == len(again),
    }
    _print(out)
    return 0 if out["PASS"] else 1


def cmd_requalify(engine: LeadEngine, args) -> int:
    """Re-check saved, not-yet-sent companies against the current rules (re-reads their sites)."""
    from concurrent.futures import ThreadPoolExecutor

    from cloudos.leadgen.enrich import Fetcher, enrich_website
    from cloudos.leadgen.qualify import qualify

    with db.get_conn() as conn:
        rows = conn.execute("SELECT company_id, company_name, website, industry, outreach_status, handoff_ref FROM companies WHERE outreach_status IN ('outreach_ready','handed_off') "
                            "AND first_contacted_at IS NULL AND NOT is_historical ORDER BY discovered_at LIMIT %s", (args.limit,)).fetchall()
        fetcher = Fetcher()
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                enrs = list(pool.map(lambda r: enrich_website(r["website"], fetcher), rows))
        finally:
            fetcher.close()
        changed = {"checked": len(rows), "still_ready": 0, "now_rejected": 0, "now_low": 0}
        pulled: list[str] = []
        for row, enr in zip(rows, enrs):
            v = qualify(name=row["company_name"], website=row["website"], industry=row["industry"] or "local",
                        enrichment=enr, chains=engine.cfg.get("chains", []))
            ostatus = ((row["outreach_status"] if row["outreach_status"] == "handed_off" else "outreach_ready") if v.outreach_ready
                       else ("rejected" if v.level == "REJECT" else "not_ready"))
            if not v.outreach_ready and (row["handoff_ref"] or "").startswith("airtable:"):
                pulled.append(row["handoff_ref"].split(":", 1)[1])
            changed["still_ready" if v.outreach_ready else ("now_rejected" if v.level == "REJECT" else "now_low")] += 1
            conn.execute("UPDATE companies SET qualification_status=%s, qualification_reason=%s, outreach_status=%s, "
                         "enriched_at=now(), updated_at=now() WHERE company_id=%s", (v.level, v.reason, ostatus, row["company_id"]))
            conn.execute("DELETE FROM contacts WHERE company_id=%s AND source <> 'history'", (row["company_id"],))
            for f in enr.emails:
                store.add_contact(conn, str(row["company_id"]), f.email, f.status, role=f.role, source="requalify", source_url=f.source_url)
        conn.commit()
    if pulled:
        from cloudos.leadgen.history_import import airtable_env
        import httpx, urllib.parse
        env = airtable_env()
        url = f"https://api.airtable.com/v0/{env['AIRTABLE_BASE_ID']}/{urllib.parse.quote(env['AIRTABLE_TABLE_NAME'])}"
        for i in range(0, len(pulled), 10):
            httpx.patch(url, headers={"Authorization": f"Bearer {env['AIRTABLE_API_KEY']}"}, timeout=60, json={
                "records": [{"id": rid, "fields": {"Status": "Rejected"}} for rid in pulled[i:i + 10]], "typecast": True,
            }).raise_for_status()
    changed["pulled_back_from_airtable"] = len(pulled)
    _print(changed)
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    p = argparse.ArgumentParser(description="Fresh lead generator")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("test"); t.add_argument("--target", type=int, default=10); t.add_argument("--max-minutes", type=float, default=20)
    t.add_argument("--sources", default=""); t.add_argument("--show", type=int, default=10)
    d = sub.add_parser("discover"); d.add_argument("--target", type=int, default=100); d.add_argument("--max-minutes", type=float, default=120)
    d.add_argument("--sources", default="")
    sub.add_parser("status")
    c = sub.add_parser("cycle"); c.add_argument("--max-minutes", type=float, default=None); c.add_argument("--force", action="store_true")
    c.add_argument("--no-handoff", action="store_true")
    i = sub.add_parser("import-history"); i.add_argument("--csv", nargs="*"); i.add_argument("--skip-gmail", action="store_true")
    i.add_argument("--gmail-since", default="01-Jan-2026")
    h = sub.add_parser("handoff"); h.add_argument("--dry-run", action="store_true")
    sub.add_parser("reports")
    r = sub.add_parser("reset-source"); r.add_argument("name")
    rq = sub.add_parser("requalify"); rq.add_argument("--limit", type=int, default=1000)
    args = p.parse_args(argv)
    db.migrate()
    engine = LeadEngine(db.get_conn)
    return {"test": cmd_test, "discover": cmd_discover, "status": cmd_status, "cycle": cmd_cycle,
            "import-history": cmd_import, "handoff": cmd_handoff, "reports": cmd_reports,
            "reset-source": cmd_reset_source, "requalify": cmd_requalify}[args.cmd](engine, args)


if __name__ == "__main__":
    sys.exit(main())
