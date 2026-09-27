"""A placeholder key left blank must not shadow a working fallback."""
from __future__ import annotations

from cloudos import config


def _settings(monkeypatch, **env):
    for key in (
        "HOSTINGER_SMTP_HOST", "HOSTINGER_SMTP_PORT",
        "HOSTINGER_SMTP_USERNAME", "HOSTINGER_SMTP_PASSWORD",
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    if hasattr(config.get_settings, "cache_clear"):
        config.get_settings.cache_clear()
    return config.get_settings()


def test_blank_hostinger_key_falls_through_to_the_working_mailbox(monkeypatch):
    s = _settings(
        monkeypatch,
        HOSTINGER_SMTP_USERNAME="",
        HOSTINGER_SMTP_PASSWORD="",
        SMTP_USER="real@example.test",
        SMTP_PASS="secret-value",
        SMTP_HOST="smtp.example.test",
        SMTP_PORT="587",
    )
    assert s.hostinger_smtp_username == "real@example.test"
    assert s.hostinger_smtp_password == "secret-value"
    assert s.hostinger_smtp_host == "smtp.example.test"
    assert s.hostinger_smtp_port == 587


def test_hostinger_wins_once_it_is_actually_filled_in(monkeypatch):
    s = _settings(
        monkeypatch,
        HOSTINGER_SMTP_USERNAME="matt@hisdomain.test",
        HOSTINGER_SMTP_PASSWORD="hostinger-secret",
        HOSTINGER_SMTP_HOST="smtp.hostinger.com",
        HOSTINGER_SMTP_PORT="465",
        SMTP_USER="old@example.test",
        SMTP_PASS="old-secret",
        SMTP_HOST="smtp.example.test",
        SMTP_PORT="587",
    )
    assert s.hostinger_smtp_username == "matt@hisdomain.test"
    assert s.hostinger_smtp_host == "smtp.hostinger.com"
    assert s.hostinger_smtp_port == 465


def test_nothing_configured_leaves_credentials_empty(monkeypatch):
    # get_settings() loads the real .env, so exercise the resolver directly:
    # this is the "fresh machine, nothing filled in" case.
    for key in ("HOSTINGER_SMTP_USERNAME", "SMTP_USER", "HOSTINGER_SMTP_PORT", "SMTP_PORT"):
        monkeypatch.delenv(key, raising=False)
    assert config._first_str("HOSTINGER_SMTP_USERNAME", "SMTP_USER") == ""
    assert config._first_int("HOSTINGER_SMTP_PORT", "SMTP_PORT", default=465) == 465


def test_blank_everywhere_still_yields_the_default(monkeypatch):
    monkeypatch.setenv("HOSTINGER_SMTP_USERNAME", "   ")
    monkeypatch.setenv("SMTP_USER", "")
    assert config._first_str("HOSTINGER_SMTP_USERNAME", "SMTP_USER", default="none") == "none"


def test_a_junk_port_does_not_crash_startup(monkeypatch):
    s = _settings(monkeypatch, HOSTINGER_SMTP_PORT="not-a-number", SMTP_PORT="587")
    assert s.hostinger_smtp_port == 587, "an unreadable port should fall through, not explode"
