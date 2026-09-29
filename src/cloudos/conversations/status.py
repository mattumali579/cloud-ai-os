"""One authoritative status per company, with explicit, checked transitions."""
from __future__ import annotations

STATUSES = (
    "discovered", "ready", "emailed", "replied", "interested", "qualified", "meeting_requested",
    "proposal_needed", "proposal_sent", "negotiating", "won", "lost", "not_now", "do_not_contact", "needs_review",
)
PRE_REPLY = {"discovered", "ready", "emailed"}          # the only statuses a cold sequence may run in
CLOSED = {"won", "lost", "do_not_contact"}

# Funnel rank: an ordinary reply never drags a company backwards
# (asking the price again while negotiating keeps it at negotiating).
RANK = {s: i for i, s in enumerate(("discovered", "ready", "emailed", "replied", "interested", "qualified",
                                    "meeting_requested", "proposal_needed", "proposal_sent", "negotiating", "won"))}

ALLOWED: dict[str, set[str]] = {
    "discovered": {"ready", "emailed", "do_not_contact", "needs_review", "lost"},
    "ready": {"emailed", "do_not_contact", "needs_review", "lost"},
    "emailed": {"replied", "interested", "qualified", "meeting_requested", "proposal_needed", "not_now", "lost",
                "do_not_contact", "needs_review"},
    "replied": {"interested", "qualified", "meeting_requested", "proposal_needed", "not_now", "lost",
                "do_not_contact", "needs_review", "negotiating"},
    "interested": {"qualified", "meeting_requested", "proposal_needed", "negotiating", "not_now", "lost",
                   "do_not_contact", "needs_review"},
    "qualified": {"meeting_requested", "proposal_needed", "negotiating", "not_now", "lost", "do_not_contact", "needs_review"},
    "meeting_requested": {"qualified", "proposal_needed", "negotiating", "not_now", "lost", "do_not_contact", "needs_review"},
    "proposal_needed": {"proposal_sent", "negotiating", "not_now", "lost", "do_not_contact", "needs_review"},
    "proposal_sent": {"negotiating", "won", "lost", "not_now", "do_not_contact", "needs_review"},
    "negotiating": {"proposal_needed", "proposal_sent", "won", "lost", "not_now", "do_not_contact", "needs_review"},
    "not_now": {"replied", "interested", "qualified", "meeting_requested", "proposal_needed", "negotiating", "lost",
                "do_not_contact", "needs_review"},
    "lost": {"replied", "interested", "meeting_requested", "proposal_needed", "do_not_contact", "needs_review"},
    "won": {"do_not_contact", "needs_review"},
    "do_not_contact": set(),            # terminal: only the owner can lift it (owner_override)
    "needs_review": set(STATUSES) - {"needs_review"},   # the owner (or a clear later reply) resolves it
}

# classification -> the status it points to (None = leave status alone)
TARGET = {
    "INTERESTED": "interested",
    "PRICE_QUESTION": "interested",
    "MORE_INFORMATION": "interested",
    "MEETING_REQUEST": "meeting_requested",
    "READY_TO_BUY": "proposal_needed",
    "OBJECTION": "negotiating",
    "NOT_NOW": "not_now",
    "REFERRAL": "replied",
    "WRONG_PERSON": "replied",
    "NOT_INTERESTED": "lost",
    "UNSUBSCRIBE": "do_not_contact",
    "AUTO_REPLY": None,
    "DELIVERY_FAILURE": None,
    "OTHER": "replied",
    "NEEDS_REVIEW": "needs_review",
}


class TransitionError(ValueError):
    pass


def check(frm: str, to: str, owner_override: bool = False) -> None:
    if to not in STATUSES:
        raise TransitionError(f"unknown status {to!r}")
    if frm == to:
        return
    if owner_override:
        return
    if to not in ALLOWED.get(frm, set()):
        raise TransitionError(f"{frm} -> {to} is not an allowed transition")


def target_for(label: str, current: str, proposal_status: str = "none") -> str:
    """Where a classified reply moves the company. Never backwards, never out of a terminal state."""
    tgt = TARGET[label]
    if current == "do_not_contact":
        return current
    if tgt is None:
        return current
    if label == "OBJECTION" and current in PRE_REPLY | {"replied"}:
        tgt = "replied" if current in PRE_REPLY else current   # a cold objection is a reply, not yet a negotiation
    if label == "READY_TO_BUY" and current in ("proposal_sent", "negotiating"):
        tgt = "negotiating"          # accepting a sent proposal: owner confirms payment, then marks won
    if tgt in RANK and current in RANK and RANK[tgt] < RANK[current]:
        return current
    if current == "won" and tgt not in ("do_not_contact", "needs_review"):
        return current
    if current == "needs_review" and tgt != "do_not_contact":
        return tgt
    return tgt if tgt in ALLOWED.get(current, set()) or tgt == current else current


def cold_sequence_allowed(status: str) -> bool:
    return status in PRE_REPLY
