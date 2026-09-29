"""Next-action engine + drafts (reply, pricing, audit, proposal).

Nothing here sends. Drafts land in outreach_drafts as awaiting_approval and
only the sender, after the owner approves, can turn one into an email.
Audits separate FACT (observed on their public website by the lead engine, or
said in the thread) from INFERENCE, and never invent revenue, traffic,
conversion rates or customer counts.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml

from cloudos.config import REPO_ROOT

OFFERS_PATH = REPO_ROOT / "config" / "brightreach_offers.yaml"

NEXT_ACTION = {
    "INTERESTED": ("reply_with_audit", "Send a short personal reply plus the mini audit"),
    "PRICE_QUESTION": ("send_pricing", "Send the pricing reply"),
    "MORE_INFORMATION": ("answer_question", "Answer their question from the offer details"),
    "MEETING_REQUEST": ("coordinate_meeting", "Coordinate the call time they asked for"),
    "READY_TO_BUY": ("send_proposal", "Send proposal / payment / onboarding step"),
    "OBJECTION": ("handle_objection", "Answer their specific concern"),
    "REFERRAL": ("contact_referral", "Contact the referred person, mentioning who referred us"),
    "NOT_NOW": ("follow_up_later", "Follow up at the time they gave"),
    "WRONG_PERSON": ("find_right_contact", "Find the right person another way"),
    "NOT_INTERESTED": ("stop", "Stop all follow-ups"),
    "UNSUBSCRIBE": ("do_not_contact", "Never contact again"),
    "AUTO_REPLY": ("none", "Nothing - wait for a human reply"),
    "DELIVERY_FAILURE": ("find_new_address", "Address bounced - find another contact if worth it"),
    "OTHER": ("owner_review", "Read and decide"),
    "NEEDS_REVIEW": ("owner_review", "Read and decide - no automated response"),
}

# who must act, per classification
ACTION_NEEDED = {
    "INTERESTED": "Approve the prepared reply + audit", "PRICE_QUESTION": "Approve the prepared pricing reply",
    "MORE_INFORMATION": "Review / approve the prepared answer", "MEETING_REQUEST": "Reply manually to set the time",
    "READY_TO_BUY": "Approve the proposal, then collect payment", "OBJECTION": "Review / approve the prepared answer",
    "REFERRAL": "Approve the intro email to the referred person", "NEEDS_REVIEW": "Read it and reply manually",
    "OTHER": "Read it", "NOT_NOW": "None", "NOT_INTERESTED": "None", "UNSUBSCRIBE": "None", "AUTO_REPLY": "None",
    "DELIVERY_FAILURE": "None", "WRONG_PERSON": "None",
}


@lru_cache(maxsize=1)
def offers() -> dict:
    return yaml.safe_load(Path(OFFERS_PATH).read_text(encoding="utf-8"))


def offer(key: str | None = None) -> dict:
    cat = offers()
    return {"key": key or cat["default_offer"], **cat["offers"][key or cat["default_offer"]]}


def _first_name(contact_email: str | None) -> str:
    return "there"   # never guess a name from an address


def _facts_by(facts: list[dict], *types: str) -> list[str]:
    return [f["fact_text"] for f in facts if f["fact_type"] in types]


def website_facts(company: dict) -> list[tuple[str, str]]:
    """(fact, evidence) pairs the lead engine actually observed on their public site."""
    p = company.get("personalization") or {}
    if isinstance(p, str):
        p = json.loads(p or "{}")
    src = p.get("evidence_url") or company.get("website") or "their website"
    out: list[tuple[str, str]] = []
    if p.get("has_contact_form") is False:
        out.append(("No contact / quote-request form was found on the pages we read.", f"{p.get('pages_read', '?')} pages of {src}"))
    elif p.get("has_contact_form") is True:
        out.append(("The site has a contact form.", src))
    if p.get("online_booking"):
        out.append((f"Booking on the site is: \"{p['online_booking']}\" (a request, not instant self-booking).", src))
    elif "no online booking" in (company.get("qualification_reason") or ""):
        out.append(("No online booking or quote request was found.", src))
    if p.get("has_chat_or_call_tracking") is False:
        out.append(("No chat, text-back or call-tracking widget was found.", src))
    if p.get("site_description") and "call" in p["site_description"].lower():
        out.append(("The site sends visitors to call the office.", f"site description on {src}"))
    return out


def next_action(label: str) -> tuple[str, str]:
    return NEXT_ACTION[label]


def pricing_reply(company: dict, state: dict, facts: list[dict], question: str) -> tuple[str, str, dict]:
    o = offer()
    h = offer("hosted")
    objections = _facts_by(facts, "price_objection")
    lines = [f"Hi {_first_name(state.get('contact_email'))},", "",
             f"Thanks for asking. The {o['name']} is {o['price_text']}. That covers:"]
    lines += [f"- {d}" for d in o["deliverables"]]
    lines += ["", f"If you'd rather not look after it yourself, {h['name']} is {h['price_text']}.", "",
              o["guarantee"], "", "Want me to send over the next step?", "", "Matt"]
    if objections:
        lines.insert(2, "(You mentioned budget earlier - happy to talk through what matters most to you first.)")
    return (f"Re: pricing for {company['company_name']}", "\n".join(lines),
            {"offer": o["key"], "price_text": o["price_text"], "question": question, "prior_price_objections": objections})


def personal_reply(company: dict, state: dict, facts: list[dict], label: str, their_words: str) -> tuple[str, str, dict]:
    o = offer()
    obs = website_facts(company)
    q = _facts_by(facts, "question")
    lines = [f"Hi {_first_name(state.get('contact_email'))},", ""]
    if label == "OBJECTION":
        lines.append("Totally fair question. [OWNER: answer the specific concern below before sending.]")
        lines.append(f"Their concern: \"{their_words}\"")
    elif label == "MORE_INFORMATION":
        lines.append(f"Good question. In short: {o['name']} does three things - " +
                     "; ".join(d.lower() for d in o["deliverables"][:3]) + ".")
        if q:
            lines.append(f"[OWNER: confirm this answers \"{q[-1]}\".]")
    else:
        lines.append(f"Glad it sounded useful. Here's what I noticed on {company.get('website') or 'your site'}:")
        lines += [f"- {f}" for f, _ in obs[:3]] or ["- [OWNER: add one specific observation]"]
        lines.append("")
        lines.append(f"That's the gap the {o['name']} closes: " + "; ".join(d.lower() for d in o["deliverables"][:3]) + ".")
    lines += ["", "Want me to send a short breakdown for your business?", "", "Matt"]
    return f"Re: {company['company_name']}", "\n".join(lines), {"label": label, "observations": obs}


def audit(company: dict, state: dict, facts: list[dict]) -> tuple[str, str, dict]:
    o = offer()
    obs = website_facts(company)
    said = _facts_by(facts, "question", "objection", "price_objection", "service_interest", "scope_exclusion",
                     "preference", "timing", "decision_maker")
    parts = [f"# Mini audit - {company['company_name']}", "",
             "## 1. What we observed (FACT)"]
    parts += [f"- {f}" for f, _ in obs] or ["- No website observations on record. [OWNER: add one before sending]"]
    parts += ["", "## 2. Evidence"]
    parts += [f"- {f} - source: {e}" for f, e in obs] or ["- none on record"]
    if said:
        parts += ["", "What they told us (FACT, from the email thread):"] + [f"- {s}" for s in said]
    parts += ["", "## 3. Why it matters (INFERENCE)",
              "- When a caller or visitor can't get an instant answer or booking, some of them contact the next company. "
              "We have NOT measured how often this happens for this business."]
    parts += ["", "## 4. Recommended BrightReach solution", f"- {o['name']}"]
    parts += ["", "## 5. Deliverables"] + [f"- {d}" for d in o["deliverables"]]
    parts += ["", "## 6. Price", f"- {o['price_text']}", f"- Optional: {offer('hosted')['name']} {offer('hosted')['price_text']}"]
    parts += ["", "## 7. Expected outcome (INFERENCE, not a promise)",
              "- Missed calls get an instant text-back and a booking link, so fewer enquiries go unanswered. "
              "No revenue or lead numbers are claimed.", f"- Guarantee: {o['guarantee']}"]
    parts += ["", "## 8. Next action", "- Reply to confirm and I'll send the setup steps."]
    excluded = _facts_by(facts, "scope_exclusion")
    if excluded:
        parts += ["", "Scope notes (they said): " + "; ".join(excluded)]
    return f"Quick audit for {company['company_name']}", "\n".join(parts), {"observations": obs, "said": said}


def proposal(company: dict, state: dict, facts: list[dict]) -> tuple[str, str, dict]:
    o = offer()
    h = offer("hosted")
    obs = website_facts(company)
    problem = obs[0][0] if obs else "[OWNER: state the problem they confirmed]"
    wants = _facts_by(facts, "service_interest")
    excluded = _facts_by(facts, "scope_exclusion")
    lines = [f"# Proposal - {company['company_name']}", "",
             f"**Identified problem:** {problem}", f"**Solution:** {o['name']}", "", "**Deliverables:**"]
    lines += [f"- {d}" for d in o["deliverables"]]
    if wants:
        lines += ["", "**What you asked for:** " + "; ".join(wants)]
    if excluded:
        lines += ["**Not included (per your request):** " + "; ".join(excluded)]
    lines += ["", f"**Setup price:** {o['price_text']}",
              f"**Recurring (optional):** {h['name']} - {h['price_text']}",
              f"**Implementation timeline:** {o.get('timeline') or '[OWNER: confirm timeline before sending]'}",
              "", "**Your part (confirm on kickoff):**"]
    lines += [f"- {r}" for r in o.get("client_responsibilities") or []]
    lines += ["", f"**Guarantee:** {o['guarantee']}", "", "**Next step:** reply \"approved\" and I'll send the invoice and kickoff steps."]
    return (f"Proposal for {company['company_name']}", "\n".join(lines),
            {"offer": o["key"], "setup": o["price_text"], "recurring": h["price_text"], "problem": problem})


def referral_intro(company: dict, referral: dict, referrer: str) -> tuple[str, str, dict]:
    who = referral.get("name") or "there"
    body = "\n".join([f"Hi {who},", "",
                      f"{referrer} at {company['company_name']} suggested I reach out to you.",
                      "[OWNER: one line on why, from the original thread]", "",
                      "Would it be worth a quick look?", "", "Matt"])
    return f"{company['company_name']} - {referrer} suggested I reach out", body, {"referral": referral}
