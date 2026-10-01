"""Database-free checks for the outreach status report's send-count source label."""
from __future__ import annotations

from datetime import date

from cloudos.conversations import reports


class _Cursor:
    def __init__(self, n: int):
        self.n = n

    def fetchone(self):
        return {"n": self.n}

    def fetchall(self):
        return []


class _Conn:
    """Synthetic connection: every count is 7, every grouped/list query is empty."""

    def __init__(self):
        self.queries: list[str] = []

    def execute(self, query, params=None):
        self.queries.append(query)
        return _Cursor(7)


def test_database_fallback_label_is_provider_agnostic():
    today = reports.status_report(_Conn(), day=date(2026, 10, 1))["today"]
    label = today["emails_confirmed_sent_source"]
    assert label == "database (provider-confirmed outreach messages)"
    assert "gmail" not in label.lower()
    assert "smtp" not in label.lower() and "hostinger" not in label.lower()
    assert today["emails_confirmed_sent"] == 7


def test_airtable_label_kept_when_airtable_is_used(monkeypatch):
    monkeypatch.setattr(reports, "airtable_emailed_count", lambda day: 3)
    today = reports.status_report(_Conn(), day=date(2026, 10, 1), use_airtable=True)["today"]
    assert today["emails_confirmed_sent_source"] == "Airtable Emailed At"
    assert today["emails_confirmed_sent"] == 3


def test_database_label_used_when_airtable_is_unavailable(monkeypatch):
    monkeypatch.setattr(reports, "airtable_emailed_count", lambda day: None)
    today = reports.status_report(_Conn(), day=date(2026, 10, 1), use_airtable=True)["today"]
    assert today["emails_confirmed_sent_source"] == "database (provider-confirmed outreach messages)"
    assert today["emails_confirmed_sent"] == 7
