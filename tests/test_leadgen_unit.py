"""Lead engine pure-logic tests (no network, no database)."""
from __future__ import annotations

from cloudos.leadgen.enrich import EmailFinding, Enrichment, extract_emails, rank_emails
from cloudos.leadgen.normalize import (
    city_state_from_address,
    normalize_domain,
    normalize_email,
    normalize_name,
    normalize_phone,
)
from cloudos.leadgen.qualify import qualify
from cloudos.leadgen.sources import GoogleMapsSource, _matches

CHAINS = ["anytime fitness", "aspen dental", "roto-rooter"]


def test_domain_identity():
    assert normalize_domain("https://www.MidCityRoofing.com/contact?x=1") == "midcityroofing.com"
    assert normalize_domain("sub.roofer.co.uk") == "roofer.co.uk"
    assert normalize_domain("https://facebook.com/JoesRoofing/") == "facebook.com/joesroofing"
    assert normalize_domain("https://joes.wixsite.com/home") == "wixsite.com/joes"
    # freemail is never a company identity (regression: all yahoo businesses merged)
    assert normalize_domain("http://yahoo.com") == ""
    assert normalize_domain("gmail.com") == ""
    assert normalize_domain("") == ""


def test_name_phone_email_normalization():
    assert normalize_name("Superior Group Construction LLC,") == normalize_name("superior group construction")
    assert normalize_name("Smith & Sons, Inc.") == "smith sons"
    assert normalize_phone("+1 (225) 217-0919") == "2252170919"
    assert normalize_phone("217-0919") == ""
    assert normalize_email("mailto:Info@Roof.com?subject=hi") == "info@roof.com"
    assert normalize_email("not an email") == ""
    assert city_state_from_address("434 S Acadian Thruway, Baton Rouge, LA 70806") == ("Baton Rouge", "LA")


def test_email_extraction_deobfuscates_and_drops_junk():
    html = """
      <a href="mailto:office@roofco.com">Email</a>
      <span class="__cf_email__" data-cfemail="%s"></span>
      <p>sales [at] roofco.com</p> <img src="logo@2x.png"> <p>user@example.com</p>
      <p>accessibility@roofco.com</p>
      <script type="application/ld+json">{"email":"hello@roofco.com"}</script>
    """ % ("2a" + bytes(b ^ 0x2A for b in b"info@roofco.com").hex())
    found = extract_emails(html)
    assert {"office@roofco.com", "info@roofco.com", "sales@roofco.com", "hello@roofco.com"} <= set(found)
    assert "user@example.com" not in found
    assert "accessibility@roofco.com" not in found
    assert not any("png" in e for e in found)


def test_rank_prefers_own_domain_generic_and_drops_third_party():
    ranked = rank_emails(["john@roofco.com", "joe@webdesignagency.com", "roofco@gmail.com", "info@roofco.com"], "www.roofco.com")
    emails = [e for e, _ in ranked]
    assert emails[0] == "info@roofco.com"
    assert "joe@webdesignagency.com" not in emails
    assert emails[-1] == "roofco@gmail.com"


def _enr(email="info@roofco.com", status="validated", facts=None, **kw):
    e = Enrichment(ok=True, website="https://roofco.com", **kw)
    if email:
        e.emails = [EmailFinding(email, status, "https://roofco.com/contact")]
    e.facts = facts if facts is not None else {"site_title": "Roof Co", "since_year": 1998}
    return e


def test_qualification_levels():
    q = lambda **kw: qualify(name=kw.pop("name", "Roof Co"), website=kw.pop("website", "https://roofco.com"),
                             industry="roofing", chains=CHAINS, **kw)
    assert q(enrichment=_enr()).level == "HIGH"
    assert q(enrichment=_enr(email="roofco@gmail.com")).level == "MEDIUM"
    assert q(enrichment=_enr(email=None)).level == "LOW"
    assert q(enrichment=_enr(status="invalid")).level == "LOW"
    assert q(enrichment=_enr(dead=True, error="unreachable")).level == "REJECT"
    assert q(enrichment=_enr(parked=True)).level == "REJECT"
    assert q(name="Anytime Fitness", enrichment=_enr()).level == "REJECT"
    assert q(website="https://www.yelp.com/biz/roof-co", enrichment=_enr()).level == "REJECT"
    assert q(website="https://www.aspendental.com/dentist/la/baton-rouge/x/", enrichment=_enr()).level == "REJECT"
    assert q(name="Gerry Lane Chevrolet", enrichment=_enr()).level == "REJECT"
    assert q(enrichment=_enr(), review_count=9000).level == "REJECT"
    assert q(website="", enrichment=_enr()).level == "REJECT"
    for level in ("HIGH", "MEDIUM"):
        assert q(enrichment=_enr() if level == "HIGH" else _enr(email="a@gmail.com")).outreach_ready


def test_legacy_isp_mailbox_is_not_outreach_ready():
    q = lambda email: qualify(
        name="Local Repair Shop",
        website="https://localrepair.example",
        industry="auto_repair",
        chains=CHAINS,
        enrichment=_enr(email=email),
    )
    legacy = q("shop@bellsouth.net")
    assert legacy.level == "LOW"
    assert legacy.outreach_ready is False
    assert "legacy ISP mailbox" in legacy.reason
    assert q("localrepair@gmail.com").outreach_ready is True


def test_osm_filter_matching():
    assert _matches({"craft": "roofer", "website": "x"}, '["craft"="roofer"]')
    assert not _matches({"craft": "plumber"}, '["craft"="roofer"]')
    assert _matches({"shop": "beauty", "beauty": "laser;skin"}, '["shop"="beauty"]["beauty"~"spa|skin|laser"]')


def test_maps_csv_parsing():
    csv_text = ('title,category,website,phone,review_count,review_rating,address,complete_address,place_id,status\n'
                '"Mid City Roofing",Roofing contractor,https://www.midcityroofing.com/,(225) 217-0919,137,4.9,'
                '"434 S Acadian Thruway, Baton Rouge, LA 70806","{""city"":""Baton Rouge"",""state"":""Louisiana""}",p1,\n')
    q = {"query": "roofing contractor in Baton Rouge, LA", "industry": "roofing", "loc": {"city": "Baton Rouge", "state": "LA"}}
    (c,) = list(GoogleMapsSource()._rows_to_candidates(csv_text, q))
    assert (c.name, c.city, c.state, c.review_count) == ("Mid City Roofing", "Baton Rouge", "LA", 137)
    assert c.normalized_domain == "midcityroofing.com"


def test_rejects_b2b_suppliers_and_city_pages_but_not_trades():
    q = lambda name, website="https://x.com": qualify(name=name, website=website, industry="plumbing",
                                                      chains=CHAINS, enrichment=_enr()).level
    assert q("Core & Main Distribution") == "REJECT"
    assert q("Woerner Landscape Supply") == "REJECT"
    assert q("Gulf Manufacturing Co") == "REJECT"
    assert qualify(name="Acme Refrigeration", website="https://acme.example", industry="air conditioning system supplier",
                   chains=CHAINS, enrichment=_enr()).level == "REJECT"
    assert q("Supreme Plumbing") == "HIGH"
    assert q("Bayou Plumbing") == "HIGH"
    assert q("Acme", "https://acme.com/landscape-supply-baton-rouge/") == "REJECT"
    assert q("Acme", "https://acme.com/branches/la-batonrouge-70809-024/") == "REJECT"
    assert q("Acme", "https://acme.com/") == "HIGH"


def test_bot_walled_site_is_low_not_dead():
    e = Enrichment(ok=False, website="https://x.com", blocked=True, error="site refuses automated reading (403)")
    v = qualify(name="Smith Law", website="https://x.com", industry="law_firm", chains=CHAINS, enrichment=e)
    assert v.level == "LOW" and "blocks automated" in v.reason
