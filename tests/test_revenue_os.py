import json
from pathlib import Path

from cloudos.revenue_os import SAFE_ACTIONS


ROOT = Path(__file__).resolve().parent.parent


def test_revenue_os_has_no_arbitrary_command_action():
    assert SAFE_ACTIONS == {
        "state",
        "decide",
        "research",
        "plan",
        "build",
        "troubleshoot",
        "notify",
        "product_verify",
        "lead_status",
        "lead_cycle",
        "outreach_qa",
        "reply_check",
        "send",
    }
    assert "command" not in SAFE_ACTIONS
    assert "shell" not in SAFE_ACTIONS


def test_master_prompt_requires_autonomy_troubleshooting_and_exact_bottlenecks():
    text = (ROOT / "docs" / "revenue_os_master_prompt.md").read_text(encoding="utf-8")
    for required in (
        "WHAT IS CURRENTLY STOPPING US FROM MAKING THE NEXT DOLLAR?",
        "Two-failure rule",
        "bottleneck_count",
        "KEEP WORKING",
        "Notify only for",
        "THE ORIGINAL MONEY GOAL CONTROLS THE SYSTEM",
    ):
        assert required in text


def test_visual_workflow_contains_full_research_build_repair_revenue_loop():
    data = json.loads((ROOT / "n8n" / "revenue_os_main.json").read_text(encoding="utf-8"))
    names = {node["name"] for node in data["nodes"]}
    required = {
        "AUTOMATIC REVENUE LOOP",
        "LOAD CURRENT STATE",
        "DECIDE NEXT MONEY ACTION",
        "COUNT + NAME BOTTLENECKS",
        "RESEARCH BEST CURRENT BUILDS",
        "TURN RESEARCH INTO ONE BUILD SPEC",
        "CLAUDE BUILD WORKER",
        "INDEPENDENT PRODUCT VERIFY",
        "TROUBLESHOOTER BOT",
        "BUILD REPAIR",
        "VERIFY REPAIR",
        "FIND NEW QUALIFIED COMPANIES",
        "DEDUPE + OUTREACH QA",
        "SEND THROUGH EXISTING SAFE SENDER",
        "CHECK + CLASSIFY REPLIES",
        "NOTIFY OWNER — IMPORTANT ONLY",
        "NEXT AUTONOMOUS LOOP",
    }
    assert required <= names
    assert data["settings"]["timezone"] == "America/Chicago"


def test_workflow_is_not_marked_active_before_live_n8n_import():
    data = json.loads((ROOT / "n8n" / "revenue_os_main.json").read_text(encoding="utf-8"))
    assert data["active"] is False
