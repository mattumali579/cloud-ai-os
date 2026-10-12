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
    assert "company name missing from body" in problems


def test_first_touch_qa_blocks_invented_numbers_speculation_and_review_gating():
    body = ("Hi there,\n\nAcme Roofing has 31 Google reviews at 4.2 stars. Odds are your happy customers "
            "just haven't been asked, and a 4.9 would guarantee top-3 placement. Want me to send the video?\n\nMatt")
    problems = rc.qa_copy("Google reviews", body, lead())
    assert "unsupported speculative claim" in problems
    assert "review gating language" in problems
    assert "unsupported numeric claim" in problems


def test_first_touch_qa_accepts_company_digits_but_not_invented_metrics():
    numbered = lead(company="Roofing 360")
    good = ("Hi there,\n\nRoofing 360 has 31 Google reviews at 4.2 stars. I recorded a short breakdown "
            "of the profile. Want me to send the video?\n\nMatt")
    assert rc.qa_copy("Google reviews", good, numbered) == []


def test_first_touch_qa_accepts_verified_one_written_as_single():
    one_review = lead(exact_evidence="Google Maps shows 4.2 stars from 1 review.", competitor_context="")
    body = ("Hi there,\n\nAcme Roofing has a single Google review. That gives a visitor very little to read "
            "before calling. I mapped a simple review and response workflow. May I send the short breakdown?\n\nMatt")
    assert rc.qa_copy("Google reviews", body, one_review) == []


def test_footer_is_outside_conversational_body():
    out = rc.add_footer("Hi there.\n\nWant the short video?\n\nMatt", "123 Main St, Austin, TX")
    assert "\n--\n123 Main St" in out
    assert out.endswith("I will not email you again.")


class _Rows:
    def __init__(self, rows=None, one=None, rowcount=1):
        self._rows, self._one, self.rowcount = list(rows or []), one, rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._one


class _PlanConn:
    def __init__(self, approved=0, copies=None):
        self.approved = approved
        self.copies = list(copies or [])
        self.sql, self.commits, self.rollbacks, self.inserts = [], 0, 0, 0

    def execute(self, sql, params=None):
        compact = " ".join(sql.split())
        self.sql.append(compact)
        if "count(*)" in compact and "outreach_campaign_copy" in compact:
            return _Rows(one={"n": self.approved})
        if compact.startswith("SELECT cc.*"):
            return _Rows(rows=list(self.copies))
        if "INSERT INTO outreach_queue" in compact:
            self.inserts += 1
            return _Rows(rowcount=1)
        return _Rows()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_candidate_query_is_bounded_and_skips_the_normalized_email_scan():
    seen = []

    class Conn:
        def execute(self, sql, params=None):
            seen.append((sql, params))
            return _Rows()

    rc._eligible_rows(Conn(), ["roofing", "hvac"], limit=60)
    sql, params = seen[0]
    assert "LIMIT %s" in sql
    assert "outreach_norm_email" not in sql
    assert params[-1] == 60
    assert params[0] == ["roofing", "hvac"]


def test_peer_query_is_the_top_two_listings_in_the_candidate_cities_only():
    seen = []

    class Conn:
        def execute(self, sql, params=None):
            seen.append((sql, params))
            return _Rows()

    assert rc._peer_rows(Conn(), ["roofing"], cities=[]) == []
    assert seen == []
    rc._peer_rows(Conn(), ["roofing"], cities=["austin"])
    sql, params = seen[0]
    assert "row_number()" in sql and "rn <= 2" in sql
    assert params[1] == ["austin"]


def test_queue_approved_keeps_ready_copy_when_replenish_is_canceled(monkeypatch):
    conn = _PlanConn(approved=0, copies=[{
        "company_id": "c1", "recipient": "owner@example.com", "company_name": "Acme Roofing",
        "subject": "Hello", "body": "Body", "copy_version": "claude-review-v1", "evidence": {},
    }])
    monkeypatch.setattr("cloudos.conversations.guard.check_fail_closed", lambda *a, **k: {"allowed": True})
    monkeypatch.setattr("cloudos.outreach.copy.qa", lambda *a, **k: [])

    def boom(*_a, **_k):
        raise RuntimeError("canceling statement due to statement timeout")

    monkeypatch.setattr(rc, "auto_prepare_campaign", boom)
    out = rc.queue_approved(conn, postal_address="123 Main St, Austin, TX")
    assert out["queued"] == 1 and out["error"] == "RuntimeError"
    assert conn.inserts == 1 and conn.commits >= 1
    # the heavy replenish never got as far as the Ready-table scan
    assert not any("outreach_norm_email" in sql for sql in conn.sql)


def test_queue_approved_does_not_replenish_when_the_buffer_is_full(monkeypatch):
    conn = _PlanConn(approved=50, copies=[])
    called = []
    monkeypatch.setattr(rc, "auto_prepare_campaign", lambda *a, **k: called.append(1))
    out = rc.queue_approved(conn, postal_address="123 Main St, Austin, TX")
    assert called == [] and "error" not in out and out["queued"] == 0

