"""Small safe boundary between n8n's visual controller and the proven revenue tools.

This module deliberately does not compose mail itself.  It delegates only to the
existing lead, sender and reply programs, records a compact proof row, and
refuses a live send unless the deployment explicitly enables it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cloudos import db

ROOT = Path(__file__).resolve().parents[2]
SAFE_ACTIONS = {"state", "product_verify", "lead_status", "lead_cycle", "outreach_qa", "reply_check", "send"}


def _run(argv: list[str], timeout: int = 180) -> dict:
    try:
        p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)
        return {"command": " ".join(argv), "exit_code": p.returncode,
                "stdout": p.stdout[-4000:], "stderr": p.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        return {"command": " ".join(argv), "exit_code": 124, "stderr": "timed out"}
    except OSError as exc:
        return {"command": " ".join(argv), "exit_code": 127, "stderr": type(exc).__name__}


def dashboard(conn) -> dict:
    from cloudos.outreach import sender, status
    from cloudos.conversations import reports
    cap = sender.daily_cap(conn, sender.load_config())
    funnel = status.funnel(conn, cap=cap)
    planner = status.planner_state(conn, funnel)
    sales = reports.status_report(conn, use_airtable=False)
    proof = conn.execute("SELECT evidence_id, action, outcome, created_at FROM revenue_os_evidence ORDER BY evidence_id DESC LIMIT 1").fetchone()
    return {
        "current_goal": "4,000 unique companies with safe, provider-confirmed outreach",
        "current_action": planner["next_action"], "system_status": "blocked" if planner["blocking"] else "running",
        "product_build_status": "verification available through bounded Claude task worker",
        "total_unique_companies": planner["discovered_total"], "qualified_leads": funnel["ready_waiting_total"],
        "ready_leads": funnel["ready_waiting_total"], "sent_today": planner["sent_today"],
        "provider_confirmed_sends_total": planner["sent_total"], "replies_total": planner["replied_total"],
        "positive_replies": planner["interested_total"], "blocked_items": planner["blocking"],
        "sales": sales.get("all_time_by_status", {}), "last_evidence": dict(proof) if proof else None,
        "next_action": planner["next_action"], "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _record(conn, execution_key: str, action: str, outcome: str, proof: dict) -> dict:
    row = conn.execute("INSERT INTO revenue_os_evidence (execution_key, action, outcome, proof) VALUES (%s,%s,%s,%s::jsonb) RETURNING evidence_id, created_at",
                       (execution_key, action, outcome, json.dumps(proof, default=str))).fetchone()
    conn.execute("INSERT INTO revenue_os_state (key, value) VALUES ('last_action', %s::jsonb) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
                 (json.dumps({"execution_key": execution_key, "action": action, "outcome": outcome, "evidence_id": row["evidence_id"]}),))
    return dict(row)


def run(action: str, *, execution_key: str | None = None, allow_send: bool = False) -> dict:
    if action not in SAFE_ACTIONS:
        raise ValueError("unknown revenue OS action")
    execution_key = execution_key or str(uuid.uuid4())
    with db.get_conn() as conn:
        if action == "state":
            proof, outcome = dashboard(conn), "verified"
        elif action == "product_verify":
            proof = _run([sys.executable, "-m", "pytest", "-q", "tests/test_claude_task.py"], timeout=240)
            outcome = "verified" if proof["exit_code"] == 0 else "failed"
        elif action == "lead_status":
            proof = _run([sys.executable, "lead_engine.py", "status"])
            outcome = "verified" if proof["exit_code"] == 0 else "blocked"
        elif action == "lead_cycle":
            # Discovery never sends mail; sender handoff is intentionally omitted here.
            proof = _run([sys.executable, "lead_engine.py", "discover", "--target", "10", "--max-minutes", "5"] , timeout=360)
            outcome = "verified" if proof["exit_code"] == 0 else "blocked"
        elif action == "outreach_qa":
            proof = _run([sys.executable, "outreach_sender.py", "plan"])
            outcome = "verified" if proof["exit_code"] == 0 else "blocked"
        elif action == "reply_check":
            proof = _run([sys.executable, "outreach_replies.py", "poll", "--quiet", "--no-drafts"])
            outcome = "verified" if proof["exit_code"] == 0 else "blocked"
        else:
            enabled = allow_send and os.environ.get("REVENUE_OS_SEND_ENABLED", "").lower() == "true"
            if not enabled:
                proof, outcome = {"reason": "live sending is disabled; existing sender protections remain in control"}, "blocked"
            else:
                proof = _run([sys.executable, "outreach_sender.py", "cycle", "--minutes", "8"], timeout=600)
                outcome = "verified" if proof["exit_code"] == 0 else "blocked"
        evidence = _record(conn, execution_key, action, outcome, proof)
        return {"execution_key": execution_key, "action": action, "outcome": outcome, "proof": proof, "evidence": evidence,
                "dashboard": dashboard(conn)}
