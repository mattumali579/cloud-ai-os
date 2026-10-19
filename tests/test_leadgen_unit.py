"""Lead engine pure-logic tests (no network, no database)."""
from __future__ import annotations

import argparse
import logging

import httpx
import pytest

from cloudos.leadgen.enrich import EmailFinding, Enrichment, extract_emails, rank_emails
from cloudos.leadgen.normalize import (
    city_state_from_address,
    normalize_domain,
    normalize_email,
    normalize_name,
    normalize_phone,
)
from cloudos.leadgen.qualify import qualify
from cloudos.leadgen.sources import GoogleMapsSource, OSMSource, SourceError, _matches

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


def test_overpass_refuses_to_start_after_the_discovery_deadline(monkeypatch):
    source = OSMSource()
    monkeypatch.setattr("cloudos.leadgen.sources.time.time", lambda: 100.0)
    with pytest.raises(SourceError, match="time budget exhausted"):
        source._overpass("[out:json];", 0, deadline=100.0)


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


def test_discover_does_not_hold_a_connection_during_network_io(monkeypatch):
    import psycopg

    from cloudos.leadgen import store
    from cloudos.leadgen.engine import LeadEngine, RunStats

    held = {"n": 0, "search": None, "enrich": None}

    class CM:
        def __enter__(self):
            held["n"] += 1
            return object()

        def __exit__(self, *_args):
            held["n"] -= 1
            return False

    class Src:
        def queries(self, cfg):
            return [{"query": "roofing austin", "industry": "roofing", "location": "Austin"}]

        def search(self, q, cfg):
            held["search"] = held["n"]
            return ["candidate"]

    monkeypatch.setattr(store, "source_available", lambda conn, name: (True, ""))
    monkeypatch.setattr(store, "next_queries", lambda conn, name, candidates, limit: list(candidates)[:limit])
    monkeypatch.setattr(store, "source_success", lambda conn, name: None)
    monkeypatch.setattr(store, "mark_query", lambda *a, **k: None)
    cfg = {"run": {"http_timeout": 1, "max_pages_per_site": 1, "enrich_workers": 1},
           "failure_policy": {}, "sources": {}, "chains": []}
    engine = LeadEngine(lambda: CM(), cfg, sources={"google_maps": Src()})

    def enrich(fresh, fetcher, deadline):
        held["enrich"] = held["n"]
        return [None]

    def save(conn, fresh, enriched, stats):
        stats.new_outreach_ready += 1
        return ["id"]

    engine._screen_candidates = lambda conn, cands, stats: ["fresh"]
    engine._enrich_candidates = enrich
    engine._store_candidates = save
    stats = engine.discover(1, 1, ["google_maps"])
    assert held["search"] == 0 and held["enrich"] == 0
    assert stats.new_outreach_ready == 1
    assert psycopg is not None and isinstance(stats, RunStats)


def test_discover_retries_when_the_pooler_closes_the_connection(monkeypatch):
    import psycopg

    from cloudos.leadgen import store
    from cloudos.leadgen.engine import LeadEngine

    calls = {"success": 0}

    class CM:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    class Src:
        def queries(self, cfg):
            return [{"query": "roofing austin", "industry": "roofing", "location": "Austin"}]

        def search(self, q, cfg):
            return ["candidate"]

    def source_success(conn, name):
        calls["success"] += 1
        if calls["success"] == 1:
            raise psycopg.OperationalError("consuming input failed: SSL connection has been closed unexpectedly")

    monkeypatch.setattr(store, "source_available", lambda conn, name: (True, ""))
    monkeypatch.setattr(store, "next_queries", lambda conn, name, candidates, limit: list(candidates)[:limit])
    monkeypatch.setattr(store, "source_success", source_success)
    monkeypatch.setattr(store, "mark_query", lambda *a, **k: None)
    cfg = {"run": {"http_timeout": 1, "max_pages_per_site": 1, "enrich_workers": 1},
           "failure_policy": {}, "sources": {}, "chains": []}
    engine = LeadEngine(lambda: CM(), cfg, sources={"google_maps": Src()})
    engine._screen_candidates = lambda conn, cands, stats: ["fresh"]
    engine._enrich_candidates = lambda fresh, fetcher, deadline: [None]
    engine._store_candidates = lambda conn, fresh, enriched, stats: stats.__setattr__("new_outreach_ready", 1) or ["id"]
    stats = engine.discover(1, 1, ["google_maps"])
    assert calls["success"] == 2 and stats.new_outreach_ready == 1


def test_save_run_retries_a_dropped_connection(monkeypatch):
    import psycopg

    import lead_engine

    calls = {"n": 0}

    class CM:
        def __enter__(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise psycopg.OperationalError("SSL connection has been closed unexpectedly")
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(lead_engine.db, "get_conn", lambda: CM())
    lead_engine._save_run(7, {"ok": True})
    assert calls["n"] == 2


API_KEY = "patSECRETVALUE"
BASE_ID = "appSECRETBASE"
BILLING_BODY = {"error": {"type": "PUBLIC_API_BILLING_LIMIT_EXCEEDED", "message": "API billing plan limit exceeded"}}


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        raise AssertionError("Airtable failure must not write the database")

    def commit(self):
        raise AssertionError("Airtable failure must not commit")


class _Client:
    def __init__(self, responses, calls):
        self._responses = list(responses)
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def request(self, method, url, **kwargs):
        self._calls.append(method)
        if not self._responses:
            raise AssertionError(f"unexpected extra {method} after Airtable already failed")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _response(status, body):
    return httpx.Response(status, json=body)


def _airtable(monkeypatch, responses, calls):
    from cloudos.leadgen import handoff

    monkeypatch.setattr(handoff.httpx, "Client", lambda **kw: _Client(responses, calls))
    return handoff.Airtable(API_KEY, BASE_ID, "Leads")


def _assert_secret_free(*chunks):
    text = "\n".join(chunks)
    assert API_KEY not in text
    assert BASE_ID not in text
    assert "api.airtable.com" not in text


def test_airtable_list_follows_every_page_then_stops():
    from cloudos.leadgen import handoff

    calls = []
    at = handoff.Airtable(API_KEY, BASE_ID, "Leads")
    at.client = _Client([
        _response(200, {"records": [{"id": "recA"}], "offset": "page-2"}),
        _response(200, {"records": [{"id": "recB"}]}),
    ], calls)
    assert [r["id"] for r in at.all(["Status"])] == ["recA", "recB"]
    assert calls == ["GET", "GET"]


@pytest.mark.parametrize("status,body,label", [
    (429, BILLING_BODY, "HTTP 429"),
    (500, {"error": {"type": "SERVER_ERROR", "message": "temporarily unavailable"}}, "HTTP 500"),
])
def test_listing_error_defers_handoff_with_one_call(monkeypatch, tmp_path, caplog, status, body, label):
    from cloudos.leadgen import handoff

    calls = []
    at = _airtable(monkeypatch, [_response(status, body)], calls)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    def _no_sleep(*_a, **_k):
        raise AssertionError("retry sleep")

    monkeypatch.setattr("time.sleep", _no_sleep)
    with caplog.at_level(logging.WARNING, logger="cloudos.leadgen"):
        result = handoff.sync_and_handoff(_Conn(), at, {"handoff": {}})
    assert calls == ["GET"]
    assert result["deferred"] is True
    assert result["airtable_errors"] == 1
    assert result["airtable_status"] == status
    assert result["handed_off"] == 0
    text = summary.read_text(encoding="utf-8")
    assert text == f"Airtable handoff deferred: 1 error ({label}). Handed off: 0. No retry.\n"
    assert "No retry" in caplog.text
    _assert_secret_free(caplog.text, text, str(result))


def test_quota_error_on_second_page_does_not_create_rows(monkeypatch, tmp_path, caplog):
    from cloudos.leadgen import handoff

    calls = []
    at = _airtable(monkeypatch, [
        _response(200, {"records": [{"id": "recA", "fields": {"Status": "Ready"}}], "offset": "page-2"}),
        _response(429, BILLING_BODY),
    ], calls)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    with caplog.at_level(logging.WARNING, logger="cloudos.leadgen"):
        result = handoff.sync_and_handoff(_Conn(), at, {"handoff": {}})
    assert calls == ["GET", "GET"]
    assert result["deferred"] is True and result["airtable_errors"] == 1 and result["handed_off"] == 0
    assert "API billing plan limit exceeded" in result["airtable_error"]
    _assert_secret_free(caplog.text, summary.read_text(encoding="utf-8"), str(result))


def test_create_stops_on_quota_and_keeps_rows_already_accepted(monkeypatch, tmp_path, caplog):
    from cloudos.leadgen import handoff

    calls = []
    created_ids = [{"id": f"rec{i:02d}"} for i in range(10)]
    at = _airtable(monkeypatch, [
        _response(200, {"records": []}),
        _response(200, {"records": created_ids}),
        _response(429, BILLING_BODY),
    ], calls)
    rows = [{
        "company_id": f"00000000-0000-0000-0000-{i:012d}",
        "company_name": f"Co {i}", "domain": f"co{i}.test", "website": f"https://co{i}.test",
        "industry": "roofing", "phone": "", "source": "osm", "qualification_level": "HIGH",
        "qualification_reason": "ok", "personalization_facts": {}, "email": f"a{i}@co{i}.test",
        "email_status": "validated", "email_source_url": f"https://co{i}.test/contact",
    } for i in range(11)]
    monkeypatch.setattr(handoff, "outreach_ready_rows", lambda conn, limit: rows)
    monkeypatch.setattr("cloudos.conversations.guard.check_fail_closed", lambda *a, **k: {"allowed": True})
    written = []

    class Conn(_Conn):
        def execute(self, sql, params=None):
            written.append(params)

        def commit(self):
            written.append("commit")

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    cfg = {"handoff": {"offer": "x", "status": "Ready", "airtable_ready_window": 20, "airtable_record_ceiling": 900}}
    with caplog.at_level(logging.WARNING, logger="cloudos.leadgen"):
        result = handoff.sync_and_handoff(Conn(), at, cfg)
    assert calls == ["GET", "POST", "POST"]
    assert result["deferred"] is True and result["airtable_errors"] == 1 and result["handed_off"] == 10
    marked = [p for p in written if isinstance(p, tuple)]
    assert len(marked) == 10
    assert {p[2] for p in marked} == {rows[i]["company_id"] for i in range(10)}
    assert "Handed off: 10" in summary.read_text(encoding="utf-8")
    _assert_secret_free(caplog.text, summary.read_text(encoding="utf-8"), str(result))


def test_transport_error_defers_without_a_second_attempt(monkeypatch, tmp_path):
    from cloudos.leadgen import handoff

    calls = []
    at = _airtable(monkeypatch, [httpx.ConnectError("down")], calls)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    result = handoff.sync_and_handoff(_Conn(), at, {"handoff": {}})
    assert calls == ["GET"]
    assert result == {
        "synced_sent": 0, "synced_unsubscribed": 0, "handed_off": 0, "deferred": True,
        "airtable_errors": 1, "airtable_status": None, "airtable_error": "request_failed",
    }
    assert summary.read_text(encoding="utf-8") == (
        "Airtable handoff deferred: 1 error (no response). Handed off: 0. No retry.\n"
    )


def test_billing_message_cannot_carry_the_key_or_base_into_logs(monkeypatch, tmp_path, caplog):
    from cloudos.leadgen import handoff

    calls = []
    leaked = {"error": {"type": "PUBLIC_API_BILLING_LIMIT_EXCEEDED",
                        "message": f"API billing plan limit exceeded key={API_KEY} base={BASE_ID}"}}
    at = _airtable(monkeypatch, [_response(429, leaked)], calls)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    with caplog.at_level(logging.WARNING, logger="cloudos.leadgen"):
        result = handoff.sync_and_handoff(_Conn(), at, {"handoff": {}})
    assert "[redacted]" in result["airtable_error"]
    _assert_secret_free(caplog.text, summary.read_text(encoding="utf-8"), str(result))


def test_handoff_command_exits_zero_when_airtable_quota_is_exceeded(monkeypatch, tmp_path, caplog, capsys):
    import lead_engine
    from cloudos.leadgen import handoff

    calls = []
    monkeypatch.setattr(handoff.httpx, "Client", lambda **kw: _Client([_response(429, BILLING_BODY)], calls))
    monkeypatch.setattr(lead_engine.db, "get_conn", lambda: _Conn())
    monkeypatch.setenv("AIRTABLE_API_KEY", API_KEY)
    monkeypatch.setenv("AIRTABLE_BASE_ID", BASE_ID)
    monkeypatch.setenv("AIRTABLE_TABLE_NAME", "Leads")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))

    def _no_sleep(*_a, **_k):
        raise AssertionError("retry sleep")

    monkeypatch.setattr("time.sleep", _no_sleep)
    with caplog.at_level(logging.WARNING, logger="cloudos.leadgen"):
        code = lead_engine.cmd_handoff(None, argparse.Namespace(dry_run=False))
    out = capsys.readouterr().out
    assert code == 0
    assert calls == ["GET"]
    assert '"deferred": true' in out and '"airtable_errors": 1' in out
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert summary == "Airtable handoff deferred: 1 error (HTTP 429). Handed off: 0. No retry.\n"
    _assert_secret_free(caplog.text, summary, out)


def test_cycle_command_exits_zero_when_handoff_is_deferred(monkeypatch, capsys):
    import lead_engine

    monkeypatch.setattr(lead_engine, "_record_run", lambda mode, fn: {"refilled": True})
    monkeypatch.setattr(lead_engine, "_handoff", lambda dry_run: {"deferred": True, "airtable_errors": 1, "handed_off": 0})
    monkeypatch.setattr(lead_engine, "cmd_reports", lambda *_a, **_k: 0)
    code = lead_engine.cmd_cycle(object(), argparse.Namespace(max_minutes=1, force=False, no_handoff=False))
    assert code == 0
    assert '"airtable_errors": 1' in capsys.readouterr().out
