"""The SMTP session must match the port, not assume Hostinger's 465."""
from __future__ import annotations

import ssl
from types import SimpleNamespace

from cloudos import email_outbox


class _FakeSSL:
    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port, self.kind = host, port, "implicit-tls"


class _FakePlain:
    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.kind = host, port, "plain"
        self.started_tls = False

    def ehlo(self):
        return None

    def starttls(self, context=None):
        self.started_tls = True

    def close(self):
        return None


def _settings(port):
    return SimpleNamespace(hostinger_smtp_host="mail.example.test", hostinger_smtp_port=port)


def test_port_465_uses_implicit_tls(monkeypatch):
    monkeypatch.setattr(email_outbox.smtplib, "SMTP_SSL", _FakeSSL)
    smtp = email_outbox._connect(_settings(465), ssl.create_default_context())
    assert smtp.kind == "implicit-tls"


def test_port_587_upgrades_with_starttls(monkeypatch):
    monkeypatch.setattr(email_outbox.smtplib, "SMTP", _FakePlain)
    smtp = email_outbox._connect(_settings(587), ssl.create_default_context())
    assert smtp.kind == "plain" and smtp.started_tls, "587 must be upgraded to TLS before login"


def test_starttls_failure_closes_the_socket(monkeypatch):
    class _Failing(_FakePlain):
        def starttls(self, context=None):
            raise OSError("no tls")

    closed = {"value": False}

    class _Tracked(_Failing):
        def close(self):
            closed["value"] = True

    monkeypatch.setattr(email_outbox.smtplib, "SMTP", _Tracked)
    try:
        email_outbox._connect(_settings(587), ssl.create_default_context())
    except OSError:
        pass
    assert closed["value"], "a failed TLS upgrade must not leak an open plaintext socket"


def test_full_send_works_over_starttls(monkeypatch, tmp_path):
    """The 587 path must complete a real send, not just open a socket."""
    import json

    from cloudos import config
    from cloudos.contracts import CloudOSError  # noqa: F401

    (tmp_path / "pending").mkdir()
    (tmp_path / "pending" / "st.json").write_text(
        json.dumps({"messages": [{"to": "a@example.test", "subject": "S", "text": "T"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("EMAIL_OUTBOX_PATH", str(tmp_path))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "true")
    monkeypatch.setenv("HOSTINGER_SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("HOSTINGER_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("HOSTINGER_SMTP_PORT", "587")
    if hasattr(config.get_settings, "cache_clear"):
        config.get_settings.cache_clear()

    delivered = []

    class _SMTP(_FakePlain):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            return None

        def send_message(self, msg):
            delivered.append(msg["To"])

    monkeypatch.setattr(email_outbox.smtplib, "SMTP", _SMTP)
    fingerprint = email_outbox.preview("st")["fingerprint"]
    result = email_outbox.send("st", fingerprint)
    assert result["sent_count"] == 1
    assert delivered == ["a@example.test"]
    if hasattr(config.get_settings, "cache_clear"):
        config.get_settings.cache_clear()
