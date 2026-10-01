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
SAFE_ACTIONS = {"state", "decide", "research", "plan", "build", "troubleshoot", "notify", "product_verify", "lead_status", "lead_cycle", "outreach_qa", "reply_check", "send"}


def _run(argv: list[str], timeout: int = 180) -> dict:
    try:
        p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)
        return {"command": " ".join(argv), "exit_code": p.returncode,
                "stdout": p.stdout[-4000:], "stderr": p.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        return {"command": " ".join(argv), "exit_code": 124, "stderr": "timed out"}
    except OSError as exc:
        return {"command": " ".join(argv), "exit_code": 127, "stderr": type(exc).__name__}


def _state_get(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM revenue_os_state WHERE key = %s", (key,)).fetchone()
    if not row:
        return default
    value = row["value"]
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _state_set(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO revenue_os_state (key, value) VALUES (%s,%s::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
        (key, json.dumps(value, default=str)),
    )


def _master_prompt() -> str:
    path = ROOT / "docs" / "revenue_os_master_prompt.md"
    return path.read_text(encoding="utf-8") if path.is_file() else "Optimize for verified revenue outcomes."


def _json_from_text(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass
    left, right = text.find("{"), text.rfind("}")
    if left >= 0 and right > left:
        try:
            value = json.loads(text[left:right + 1])
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _codex_json(prompt: str, *, web_search: bool = False, timeout: int = 600) -> dict:
    try:
        from cloudos.router.providers import codex_cli
        response = codex_cli.generate(prompt, max_tokens=4096, timeout=timeout, web_search=web_search)
        parsed = _json_from_text(response.text)
        if not parsed:
            return {"ok": False, "error": "Codex returned no parseable JSON", "raw": response.text[-2000:]}
        return {"ok": True, "data": parsed, "auth_mode": response.auth_mode}
    except Exception as exc:  # fail closed; provider layer already scrubs metered credentials
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"}


def _planner_bottlenecks(planner: dict) -> list[dict]:
    out = []
    for i, item in enumerate(planner.get("blocking") or [], 1):
        low = str(item).lower()
        requires_owner = any(word in low for word in ("password", "login", "mfa", "2fa", "credential"))
        out.append({
            "id": f"pipeline-{i}",
            "severity": "high" if requires_owner else "medium",
            "layer": "SERVICE" if requires_owner else "RESULT",
            "description": str(item),
            "evidence": str(item),
            "attempt_count": 0,
            "owner": "human" if requires_owner else "troubleshooter",
            "next_action": "owner action required" if requires_owner else "diagnose and work around",
            "requires_owner": requires_owner,
        })
    return out


def dashboard(conn) -> dict:
    from cloudos.outreach import sender, status
    from cloudos.conversations import reports
    cap = sender.daily_cap(conn, sender.load_config())
    funnel = status.funnel(conn, cap=cap)
    planner = status.planner_state(conn, funnel)
    sales = reports.status_report(conn, use_airtable=False)
    proof = conn.execute(
        "SELECT evidence_id, action, outcome, created_at FROM revenue_os_evidence "
        "ORDER BY evidence_id DESC LIMIT 1"
    ).fetchone()
    runtime = _state_get(conn, "revenue_os_runtime_bottlenecks", []) or []
    bottlenecks = _planner_bottlenecks(planner) + [b for b in runtime if isinstance(b, dict)]
    return {
        "current_goal": _state_get(
            conn,
            "revenue_os_goal",
            "Build and operate the service-business lead conversion product and acquire paying customers.",
        ),
        "current_action": planner["next_action"],
        "system_status": "blocked" if any(b.get("requires_owner") for b in bottlenecks) else "running",
        "product_build_status": _state_get(conn, "product_build_status", "research_pending"),
        "product_research_complete": bool(_state_get(conn, "product_research")),
        "product_spec_ready": bool(_state_get(conn, "product_build_spec")),
        "product_last_build": _state_get(conn, "product_last_build"),
        "total_unique_companies": planner["discovered_total"],
        "qualified_leads": funnel["ready_waiting_total"],
        "ready_leads": funnel["ready_waiting_total"],
        "sent_today": planner["sent_today"],
        "provider_confirmed_sends_total": planner["sent_total"],
        "replies_total": planner["replied_total"],
        "positive_replies": planner["interested_total"],
        "blocked_items": [b["description"] for b in bottlenecks],
        "bottleneck_count": len(bottlenecks),
        "bottlenecks": bottlenecks,
        "sales": sales.get("all_time_by_status", {}),
        "last_evidence": dict(proof) if proof else None,
        "next_action": _state_get(conn, "revenue_os_decision", planner["next_action"]),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _decide(conn) -> dict:
    d = dashboard(conn)
    research = _state_get(conn, "product_research")
    spec = _state_get(conn, "product_build_spec")
    build = _state_get(conn, "product_last_build") or {}

    # Money already on the table beats new engineering.
    if d["positive_replies"] > 0:
        decision = {"action": "PROCESS_REPLIES", "why": f"{d['positive_replies']} positive/interested prospect(s) exist", "branch": "revenue"}

    # Human-only blockers notify the owner; executable failures go to the troubleshooter.
    elif any(b.get("requires_owner") for b in d["bottlenecks"]):
        decision = {"action": "NOTIFY", "why": "one or more blockers require owner credentials/authorization", "branch": "notify"}
    elif d["bottleneck_count"] > 0:
        decision = {"action": "TROUBLESHOOT", "why": f"{d['bottleneck_count']} executable bottleneck(s) detected", "branch": "troubleshoot"}

    # Bootstrap the product research/build loop exactly once, then improve it when later bottlenecks demand it.
    elif not research:
        decision = {"action": "RESEARCH", "why": "product competitor teardown has not been completed", "branch": "product"}
    elif not spec:
        decision = {"action": "PLAN", "why": "research exists but no bounded build milestone is approved", "branch": "product"}
    elif not build.get("verified"):
        decision = {"action": "BUILD", "why": "a product build spec is ready but the milestone is not independently verified", "branch": "product"}

    # After the product milestone is verified, keep the revenue engine moving without asking for GO.
    elif d["ready_leads"] > 0:
        decision = {"action": "SEND", "why": f"{d['ready_leads']} qualified/ready companies are available", "branch": "revenue"}
    elif d["total_unique_companies"] < 4000:
        decision = {"action": "FIND_LEADS", "why": f"{4000 - d['total_unique_companies']} companies remain to the 4,000-company pool", "branch": "revenue"}
    else:
        decision = {"action": "MEASURE", "why": "core product milestone is verified and acquisition inventory target is met", "branch": "verify"}

    _state_set(conn, "revenue_os_decision", decision)
    return decision


def _research(conn) -> dict:
    d = dashboard(conn)
    prompt = _master_prompt() + "\n\nCURRENT VERIFIED STATE:\n" + json.dumps(d, default=str, indent=2) + """
\nTASK: Research current best public implementations of the service-business lead-conversion product described above.
Use web search. Study Podium, HighLevel, Hatch, ServiceTitan, Jobber, Housecall Pro and any stronger current example.
Return JSON ONLY with this schema:
{
  "summary": "...",
  "facts": [{"fact":"...","source":"https://..."}],
  "strongest_patterns": ["..."],
  "weaknesses_to_avoid": ["..."],
  "recommended_milestone": "...",
  "why_now": "...",
  "acceptance_test": "..."
}
Do not fabricate facts or URLs. Research enough to choose one build milestone, then stop."""
    result = _codex_json(prompt, web_search=True, timeout=900)
    if result.get("ok"):
        _state_set(conn, "product_research", result["data"])
        _state_set(conn, "product_build_status", "research_verified")
    return result


def _plan(conn) -> dict:
    d = dashboard(conn)
    research = _state_get(conn, "product_research", {})
    prompt = _master_prompt() + "\n\nCURRENT STATE:\n" + json.dumps(d, default=str, indent=2)
    prompt += "\n\nVERIFIED RESEARCH:\n" + json.dumps(research, default=str, indent=2)
    prompt += """
\nChoose ONE smallest high-value product milestone that advances the researched lead→conversation→calendar→proof product.
Return JSON ONLY as a bounded Claude worker task spec:
{
  "key":"short-stable-slug",
  "objective":"one concrete outcome",
  "files":["existing/path/or/directory"],
  "constraints":["preserve existing working systems","do not weaken safety/compliance/proof gates"],
  "success_test":"python -m pytest tests/test_revenue_os.py -q",
  "evidence":["specific observable evidence"]
}
Use only a success_test accepted by scripts/claude_task.py. Do not request sending email, pushing git, paid purchases or secrets."""
    result = _codex_json(prompt, web_search=False, timeout=600)
    if result.get("ok"):
        spec = result["data"]
        required = ("key", "objective", "files", "constraints", "success_test", "evidence")
        if not all(spec.get(k) for k in required):
            return {"ok": False, "error": "planner returned incomplete build spec", "data": spec}
        _state_set(conn, "product_build_spec", spec)
        _state_set(conn, "product_build_status", "spec_ready")
    return result


def _build(conn) -> dict:
    spec = _state_get(conn, "product_build_spec")
    if not isinstance(spec, dict):
        return {"ok": False, "error": "no product_build_spec exists"}
    task_dir = ROOT / "logs" / "revenue_os"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_file = task_dir / "next_task.json"
    task_file.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    run = _run([sys.executable, "scripts/claude_task.py", "--spec", str(task_file)], timeout=1200)
    parsed = _json_from_text(run.get("stdout", ""))
    proof = {"runner": run, "report": parsed}
    verified = bool(parsed.get("verified")) and parsed.get("status") == "verified"
    _state_set(conn, "product_last_build", {**parsed, "verified": verified})
    _state_set(conn, "product_build_status", "verified" if verified else parsed.get("status", "failed"))
    return {"ok": verified, "verified": verified, **proof}


def _troubleshoot(conn) -> dict:
    d = dashboard(conn)
    last = _state_get(conn, "product_last_build", {}) or {}
    attempts = int(_state_get(conn, "troubleshooter_attempts", 0) or 0) + 1
    _state_set(conn, "troubleshooter_attempts", attempts)
    architecture_change = attempts >= 2
    prompt = _master_prompt() + "\n\nTROUBLESHOOT THIS VERIFIED FAILURE STATE:\n" + json.dumps(
        {"bottlenecks": d["bottlenecks"], "last_build": last, "attempt": attempts},
        default=str,
        indent=2,
    )
    prompt += """
\nReturn JSON ONLY as a corrected bounded Claude worker task spec with:
key, objective, files, constraints, success_test, evidence, new_information.
Classify the broken layer and put the exact failure evidence into new_information.
"""
    if architecture_change:
        prompt += "\nTHIS APPROACH HAS ALREADY FAILED TWICE. Do NOT repeat the same fix. Change the underlying implementation path/assumption while preserving the original money goal."
    else:
        prompt += "\nUse the smallest evidence-based repair. Do not broaden scope."
    result = _codex_json(prompt, web_search=architecture_change, timeout=700)
    if result.get("ok"):
        spec = result["data"]
        if not spec.get("new_information"):
            spec["new_information"] = f"Troubleshooter attempt {attempts} based on latest recorded failure evidence."
        _state_set(conn, "product_build_spec", spec)
        _state_set(conn, "product_build_status", "repair_spec_ready")
        # Runtime bottleneck is now owned by the repair attempt; planner blockers remain visible separately.
        _state_set(conn, "revenue_os_runtime_bottlenecks", [])
    return {**result, "attempt": attempts, "architecture_change": architecture_change}


def _notify(conn, execution_key: str) -> dict:
    from cloudos.outreach import agentmail, sender
    cfg = sender.load_config()
    d = dashboard(conn)
    reasons = d["bottlenecks"]
    if d["positive_replies"] <= 0 and not reasons:
        return {"created": False, "delivered": False, "reason": "nothing important to notify"}
    if d["positive_replies"] > 0:
        subject = f"BrightReach: {d['positive_replies']} important prospect(s)"
    else:
        subject = f"Revenue OS: {len(reasons)} bottleneck(s)"
    details = "\n".join(
        f"{i}. [{b.get('layer')}] {b.get('description')} | next: {b.get('next_action')}"
        for i, b in enumerate(reasons, 1)
    ) or "Positive prospect activity needs attention."
    return agentmail.notify(
        conn,
        cfg,
        role="manager",
        dedupe_key=f"revenue-os:{execution_key}:{subject}",
        severity="high",
        subject=subject,
        text=(
            f"Revenue OS important event.\n\nBottleneck count: {len(reasons)}\n{details}\n\n"
            f"Positive replies: {d['positive_replies']}\n"
            f"Evidence: {d.get('last_evidence')}"
        ),
    )


def _set_runtime_bottleneck(conn, action: str, outcome: str, proof: dict) -> None:
    if outcome == "verified":
        if action in {"build", "troubleshoot", "product_verify"}:
            _state_set(conn, "revenue_os_runtime_bottlenecks", [])
        return
    prior = _state_get(conn, "revenue_os_runtime_bottlenecks", []) or []
    attempts = 1 + sum(1 for b in prior if isinstance(b, dict) and b.get("id") == f"runtime-{action}")
    item = {
        "id": f"runtime-{action}",
        "severity": "high",
        "layer": "AGENT" if action in {"research", "plan", "build", "troubleshoot"} else "ACTION",
        "description": f"{action} did not verify",
        "evidence": str(proof.get("error") or proof.get("report") or proof)[-1200:],
        "attempt_count": attempts,
        "owner": "troubleshooter",
        "next_action": "change approach" if attempts >= 2 else "diagnose and retry once",
        "requires_owner": False,
    }
    keep = [b for b in prior if isinstance(b, dict) and b.get("id") != item["id"]]
    _state_set(conn, "revenue_os_runtime_bottlenecks", keep + [item])


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
        elif action == "decide":
            proof, outcome = _decide(conn), "verified"
        elif action == "research":
            proof = _research(conn)
            outcome = "verified" if proof.get("ok") else "blocked"
        elif action == "plan":
            proof = _plan(conn)
            outcome = "verified" if proof.get("ok") else "blocked"
        elif action == "build":
            proof = _build(conn)
            outcome = "verified" if proof.get("verified") else "failed"
        elif action == "troubleshoot":
            proof = _troubleshoot(conn)
            outcome = "verified" if proof.get("ok") else "blocked"
        elif action == "notify":
            proof = _notify(conn, execution_key)
            # A queued notice is real durable state but not delivered proof.
            outcome = "verified" if proof.get("delivered") or not proof.get("created") else "blocked"
        elif action == "product_verify":
            proof = _run([sys.executable, "-m", "pytest", "-q", "tests/test_claude_task.py", "tests/test_revenue_os.py"], timeout=300)
            outcome = "verified" if proof["exit_code"] == 0 else "failed"
        elif action == "lead_status":
            proof = _run([sys.executable, "lead_engine.py", "status"])
            outcome = "verified" if proof["exit_code"] == 0 else "blocked"
        elif action == "lead_cycle":
            proof = _run([sys.executable, "lead_engine.py", "discover", "--target", "10", "--max-minutes", "5"], timeout=360)
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

        _set_runtime_bottleneck(conn, action, outcome, proof if isinstance(proof, dict) else {"proof": proof})
        evidence = _record(conn, execution_key, action, outcome, proof)
        return {
            "execution_key": execution_key,
            "action": action,
            "outcome": outcome,
            "proof": proof,
            "evidence": evidence,
            "dashboard": dashboard(conn),
        }
