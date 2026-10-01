from cloudos.revenue_os import SAFE_ACTIONS


def test_revenue_os_has_no_arbitrary_command_action():
    assert SAFE_ACTIONS == {"state", "product_verify", "lead_status", "lead_cycle", "outreach_qa", "reply_check", "send"}
