"""Important-email -> Discord alerting.

Runs in GitHub Actions (cloud), never on the laptop. The mailbox is read over
IMAP, every message is scored by :mod:`cloudos.email_alerts.filter`, and only
messages that clear the bar are pushed to Discord.
"""
from .filter import Classification, classify, is_important

__all__ = ["Classification", "classify", "is_important"]
