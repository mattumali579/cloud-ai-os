"""Markdown reports. Public repo: counts only, emails masked, no raw lead lists."""
from __future__ import annotations

from datetime import datetime, timezone

from cloudos.leadgen import store


def mask_email(email: str) -> str:
    local, _, dom = (email or "").partition("@")
    return f"{local[:1]}***@{dom}" if dom else ""


def source_performance(conn) -> list[dict]:
    rows = conn.execute(
        """
        WITH d AS (
          SELECT source,
                 count(*)                                        AS results,
                 count(*) FILTER (WHERE outcome = 'duplicate')   AS duplicates,
                 count(*) FILTER (WHERE outcome IN ('new','rejected')) AS new_companies
          FROM discovery_history WHERE source <> 'dedupe_test' GROUP BY source),
        c AS (
          SELECT co.discovery_source AS source,
                 count(*) FILTER (WHERE EXISTS (SELECT 1 FROM contacts ct WHERE ct.company_id = co.company_id)) AS with_email,
                 count(*) FILTER (WHERE co.qualification_status IN ('HIGH','MEDIUM')) AS qualified,
                 count(*) FILTER (WHERE co.qualification_status = 'REJECT') AS rejected,
                 count(*) FILTER (WHERE EXISTS (SELECT 1 FROM contacts ct WHERE ct.company_id = co.company_id
                                                AND ct.email_status = 'invalid')) AS invalid_email,
                 count(*) FILTER (WHERE EXISTS (SELECT 1 FROM outreach_history oh WHERE oh.company_id = co.company_id
                                                AND oh.status = 'replied')) AS replied,
                 count(*) FILTER (WHERE co.first_contacted_at IS NOT NULL) AS contacted
          FROM companies co WHERE NOT co.is_historical GROUP BY co.discovery_source)
        SELECT d.*, coalesce(c.with_email,0) with_email, coalesce(c.qualified,0) qualified,
               coalesce(c.rejected,0) rejected, coalesce(c.invalid_email,0) invalid_email,
               coalesce(c.contacted,0) contacted, coalesce(c.replied,0) replied,
               h.consecutive_failures, h.total_failures, h.disabled_until, h.last_error,
               (SELECT count(*) FROM lead_queries q WHERE q.source = d.source) AS queries_run
        FROM d LEFT JOIN c USING (source) LEFT JOIN lead_source_health h USING (source)
        ORDER BY d.source
        """).fetchall()
    # history_recovery updates existing rows, so its qualified count comes from contacts it added
    for r in rows:
        if r["source"] == "history_recovery":
            rec = conn.execute(
                "SELECT count(DISTINCT company_id) FILTER (WHERE TRUE) AS with_email FROM contacts WHERE source = 'history_recovery'").fetchone()
            q = conn.execute("SELECT count(*) AS n FROM companies WHERE is_historical AND qualification_reason LIKE 'recovered:%%' "
                             "AND qualification_status IN ('HIGH','MEDIUM')").fetchone()
            r["with_email"], r["qualified"] = rec["with_email"], q["n"]
    return rows


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "-"


def performance_markdown(conn) -> str:
    rows = source_performance(conn)
    lines = [
        "# Lead source performance", "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from the live database (`discovery_history`, `companies`, `contacts`).",
        "", "| Source | Queries | Results | Duplicate rate | New companies | With email | Qualified | Qualified rate | Invalid email | Contacted | Replies | Health |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        health = "ok" if not r["consecutive_failures"] else f"{r['consecutive_failures']} straight failures"
        if r["disabled_until"] and r["disabled_until"] > datetime.now(timezone.utc):
            health = f"benched until {r['disabled_until']:%m-%d %H:%M} UTC"
        new = r["new_companies"]
        label = "history_recovery (old never-contacted companies re-checked, not new)" if r["source"] == "history_recovery" else r["source"]
        lines.append(f"| {label} | {r['queries_run']} | {r['results']} | {_pct(r['duplicates'], r['results'])} | {new} | "
                     f"{r['with_email']} | {r['qualified']} | {_pct(r['qualified'], new)} | {r['invalid_email']} | "
                     f"{r['contacted']} | {r['replied']} | {health} |")
    lines += ["", "Reply rate needs the sender to write replies back; until it does, Replies stays 0 and says nothing about lead quality."]
    return "\n".join(lines) + "\n"


def status_markdown(conn, cfg: dict) -> str:
    inv = store.inventory(conn)
    goal = cfg["inventory"]["total_goal"]
    last = conn.execute("SELECT mode, started_at, finished_at, stats FROM lead_runs ORDER BY started_at DESC LIMIT 5").fetchall()
    lines = [
        "# Lead engine status", "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from the live database.", "",
        "| Measure | Count |", "|---|---|",
        f"| Historical companies (imported from old ledgers) | {inv['historical_companies']} |",
        f"| Newly discovered companies | {inv['discovered_companies']} |",
        f"| Total unique companies | {inv['total_companies']} |",
        f"| Domains indexed for dedupe | {inv['domains_indexed']} |",
        f"| Never-contacted companies | {inv['uncontacted']} |",
        f"| Qualified (HIGH+MEDIUM), all | {inv['qualified']} |",
        f"| Qualified NEW companies (counts toward goal) | {inv['qualified_new']} |",
        f"| Outreach-ready, waiting in queue | {inv['outreach_ready']} |",
        f"| Handed to sender, not yet sent | {inv['handed_off']} |",
        f"| Contacted (provider-confirmed) | {inv['contacted']} |",
        f"| Duplicates rejected at discovery | {inv['duplicates_rejected']} |",
        f"| Rejected (chain, dead, directory...) | {inv['rejected']} |",
        f"| Invalid contacts | {inv['invalid_contacts']} |",
        f"| Progress toward {goal:,} unique qualified | {inv['qualified_new']} / {goal:,} ({100 * inv['qualified_new'] / goal:.1f}%) |",
        "", f"Refill rule: when ready+handed-off inventory ({inv['available_inventory']}) drops below "
        f"{cfg['inventory']['low_watermark']}, discover until it is back to {cfg['inventory']['target_ready']}.", "",
        "## Recent runs", "", "| Mode | Started (UTC) | Minutes | New companies | New outreach-ready | Duplicates | Source errors |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in last:
        s = r["stats"] or {}
        st = s.get("stats", s)
        mins = f"{(r['finished_at'] - r['started_at']).total_seconds() / 60:.0f}" if r["finished_at"] else "running"
        if s.get("error"):
            lines.append(f"| {r['mode']} | {r['started_at']:%m-%d %H:%M} | {mins} | failed: {s['error'][:60]} | - | - | - |")
            continue
        lines.append(f"| {r['mode']} | {r['started_at']:%m-%d %H:%M} | {mins} | {st.get('new_companies', '-')} | "
                     f"{st.get('new_outreach_ready', '-')} | {st.get('duplicates', '-')} | {len(st.get('source_errors') or {})} |")
    return "\n".join(lines) + "\n"
