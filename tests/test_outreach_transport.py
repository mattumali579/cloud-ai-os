"""The Hostinger SMTP client against a REAL local SMTP server (aiosmtpd): accepted,
bad address, bad login, and a connection that dies while the message is handed over."""
from __future__ import annotations

import smtplib
import socket

import pytest

aiosmtpd = pytest.importorskip("aiosmtpd")
from aiosmtpd.controller import Controller  # noqa: E402
from aiosmtpd.smtp import AuthResult, LoginPassword  # noqa: E402

from cloudos.outreach.transport import Mailbox, build, new_message_id  # noqa: E402


class Handler:
    def __init__(self):
        self.got = []
        self.drop_data = False

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if address.startswith("quota@"):
            return "550 5.4.5 Daily user sending quota exceeded"
        if address.startswith("nobody@"):
            return "550 5.1.1 <nobody@x.test>: Recipient address rejected: User unknown"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        if self.drop_data:
            server.transport.close()           # the connection dies mid hand-over
            return None
        self.got.append(envelope)
        return "250 2.0.0 Ok: queued as ABC123"


def _auth(server, session, envelope, mechanism, auth_data):
    ok = isinstance(auth_data, LoginPassword) and auth_data.password == b"right"
    return AuthResult(success=ok)


@pytest.fixture
def smtp():
    h = Handler()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    c = Controller(h, hostname="127.0.0.1", port=port, authenticator=_auth, auth_require_tls=False)
    c.start()
    yield h, port
    c.stop()


def _box(port, pw="right"):
    return Mailbox(user="matt@fitnesshubb.test", password=pw, smtp_host="127.0.0.1", smtp_port=port,
                   smtp_factory=lambda host, port, timeout, context: smtplib.SMTP(host, port, timeout=timeout))


def _msg(to):
    mid = new_message_id("matt@fitnesshubb.test")
    return build(from_email="matt@fitnesshubb.test", from_name="Sam Sender", to=to, subject="Hi",
                 body="hello there", message_id=mid, in_reply_to="root@fitnesshubb.test", step=1), mid


def test_accepted_message_carries_threading_and_opt_out(smtp):
    h, port = smtp
    m, mid = _msg("owner@acme.test")
    r = _box(port).send(m)
    assert r.ok and r.kind == "sent" and len(h.got) == 1
    raw = h.got[0].content.decode()
    assert f"<{mid}>" in raw and "In-Reply-To: <root@fitnesshubb.test>" in raw and "List-Unsubscribe:" in raw


def test_unknown_recipient_is_permanent(smtp):
    h, port = smtp
    r = _box(port).send(_msg("nobody@x.test")[0])
    assert not r.ok and r.kind == "permanent" and "550" in r.detail and h.got == []


def test_wrong_password_is_auth(smtp):
    _, port = smtp
    r = _box(port, pw="wrong").send(_msg("owner@acme.test")[0])
    assert not r.ok and r.kind == "auth"


def test_connection_dying_during_data_is_unknown_not_transient(smtp):
    h, port = smtp
    h.drop_data = True
    r = _box(port).send(_msg("owner@acme.test")[0])
    assert not r.ok and r.kind == "unknown"


def test_server_down_before_anything_is_transient():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    r = _box(port).send(_msg("owner@acme.test")[0])
    assert not r.ok and r.kind == "transient"


def test_sending_limit_is_throttled_not_a_bounce(smtp):
    h, port = smtp
    r = _box(port).send(_msg("quota@acme.test")[0])
    assert not r.ok and r.kind == "throttled"


def test_reconnects_when_an_idle_connection_was_closed(smtp):
    h, port = smtp
    box = _box(port)
    assert box.send(_msg("owner@acme.test")[0]).ok
    box._smtp.sock.close()                     # the server dropped the idle connection between sends
    assert box.send(_msg("owner@acme.test")[0]).ok and len(h.got) == 2
