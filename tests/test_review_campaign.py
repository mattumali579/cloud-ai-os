from cloudos.outreach import review_campaign as rc


def lead(**overrides):
    base = {
        "company_id": "00000000-0000-0000-0000-000000000001",
        "company": "Acme Roofing",
        "city": "Austin",
        "state": "TX",
        "exact_evidence": "Google Maps shows 4.2 stars from 31 reviews.",
        "competitor_context": "Best Roof Co in Austin has 210 Google reviews at 4.8 stars.",
    }
    base.update(overrides)
    return base


def test_score_uses_verified_review_and_competitor_gap():
    row = {"company_id": "1", "company_name": "Acme Roofing", "city": "Austin", "state": "TX",
           "industry": "roofing", "personalization": {"google_rating": 4.2, "google_reviews": 31}}
    competitor = {"company_name": "Best Roof Co", "city": "Austin",
                  "personalization": {"google_rating": 4.8, "google_reviews": 210}}
    result = rc.score(row, competitor)
    assert result.value >= 7
    assert "31" in result.exact_evidence
    assert "Best Roof Co" in result.competitor_context
    assert "210" in result.email_hook


def test_first_touch_qa_accepts_short_grounded_video_cta():
    body = ("Hi there,\n\nI noticed Acme Roofing has 31 Google reviews at 4.2 stars, while another local "
            "roofer has 210. That gap can make a solid company look less established in Maps. I recorded a short "
            "breakdown of what I would change first. Want me to send the video?\n\nMatt")
    assert rc.qa_copy("Google reviews", body, lead()) == []


def test_first_touch_qa_blocks_old_offer_links_and_unsupported_copy():
    body = ("Hi there,\n\nWe help businesses leverage AI for missed-call rescue at $497. "
            "See https://example.com. Want me to send it?\n\nMatt")
    problems = rc.qa_copy("Quick question", body, lead())
    assert "old offer language" in problems
    assert "link in first touch" in problems
    assert "generic marketing language" in problems
    assert "no verifiable evidence from the lead record" in problems


def test_footer_is_outside_conversational_body():
    out = rc.add_footer("Hi there.\n\nWant the short video?\n\nMatt", "123 Main St, Austin, TX")
    assert "\n--\n123 Main St" in out
    assert out.endswith("I will not email you again.")

