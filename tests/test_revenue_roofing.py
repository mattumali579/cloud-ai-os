from cloudos.revenue.roofing import RoofingProspect, build_outreach, score_roofing_prospect


def test_rejects_non_roofer():
    p = RoofingProspect(company="Acme", city="Baton Rouge", state="LA", residential_roofing=False, active_service_area=True)
    result = score_roofing_prospect(p)
    assert result.qualified is False
    assert result.score == 0


def test_qualified_ideal_roofer():
    p = RoofingProspect(
        company="Pelican Roofing", city="Baton Rouge", state="LA", website="https://example.com",
        employee_count=18, residential_roofing=True, active_service_area=True,
        has_google_business_profile=True, has_meta_ads=False, instant_lead_followup=False,
        owner_or_decision_maker="Jordan Smith", observation="Your site pushes phone calls but I couldn't find an instant booking path.",
    )
    result = score_roofing_prospect(p)
    assert result.qualified is True
    assert result.score == 100


def test_outreach_sells_end_state_not_ai_tools():
    p = RoofingProspect(
        company="Pelican Roofing", city="Baton Rouge", state="LA", residential_roofing=True,
        active_service_area=True, owner_or_decision_maker="Jordan Smith", observation="I saw your Baton Rouge roofing page.",
    )
    msg = build_outreach(p)
    text = (msg["subject"] + " " + msg["body"]).lower()
    assert "booked roof inspections" in text
    assert "qualified homeowners" in text
    assert "higgsfield" not in text
    assert "n8n" not in text
    assert "ai " not in text


def test_outreach_does_not_guarantee_revenue():
    p = RoofingProspect(company="Acme Roof", city="Baton Rouge", state="LA", residential_roofing=True, active_service_area=True)
    text = build_outreach(p)["body"].lower()
    assert "guarantee" not in text
    assert "make you" not in text
