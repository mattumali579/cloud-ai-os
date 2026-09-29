"""Reply classification that reads the whole conversation, not one sentence.

Deterministic rules, deliberately conservative. Every label carries the exact
words that triggered it (evidence). When signals conflict, when nothing clear
matches, or when a short reply ("Yeah.") can't be tied to one specific
question we asked, the answer is NEEDS_REVIEW - never a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from cloudos.conversations.text import EMAIL_RE, addr, one_line, strip_quoted

VERSION = "rules-2026-09-29.1"
LABELS = (
    "INTERESTED", "PRICE_QUESTION", "MORE_INFORMATION", "MEETING_REQUEST", "READY_TO_BUY", "OBJECTION",
    "NOT_NOW", "REFERRAL", "WRONG_PERSON", "NOT_INTERESTED", "UNSUBSCRIBE", "AUTO_REPLY",
    "DELIVERY_FAILURE", "OTHER", "NEEDS_REVIEW",
)
MIN_CONFIDENCE = 0.70
POSITIVE = {"INTERESTED", "PRICE_QUESTION", "MORE_INFORMATION", "MEETING_REQUEST", "READY_TO_BUY"}
NEGATIVE = {"NOT_INTERESTED", "UNSUBSCRIBE"}


def _p(*patterns: str) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


RULES: dict[str, list[tuple[re.Pattern, float]]] = {}


def _rule(label: str, conf: float, *patterns: str, flags: int = re.I) -> None:
    RULES.setdefault(label, []).extend((re.compile(p, flags), conf) for p in patterns)


# Order inside a label doesn't matter; order across labels is PRIORITY below.
_rule("UNSUBSCRIBE", 0.97,
      r"\bunsubscribe\b", r"\bremove (me|us|my email|our email|this email)\b", r"\btake (me|us) off\b",
      r"\bstop (emailing|e-mailing|contacting|sending|messaging)\b", r"\bdo not (contact|email|e-mail) (me|us)\b",
      r"\bdon'?t (contact|email|e-mail) (me|us)\b", r"\bopt(ed)?[- ]?out\b", r"\bno more emails?\b",
      r"\bnever (contact|email) (me|us)\b", r"\blose (my|our) (email|address)\b",
      r"\b(this is|stop|reported (this |you )?as|you'?re) spam(ming)?\b", r"\bspammers?\b",
      r"\bcease and desist\b", r"\bleave (me|us) alone\b")
_rule("NOT_INTERESTED", 0.92,
      r"\bnot interested\b", r"\bno,? thanks?\b", r"\bno thank you\b", r"\bnot (a fit|for us|for me)\b",
      r"\b(we'?re|we are|i'?m|i am) (all set|good|covered)\b", r"\bwe'?ll pass\b", r"\bgoing to pass\b",
      r"\bno need for (this|it|that|your)\b", r"\b(don'?t|do not) need (this|it|that|your|any)\b", r"\bnot looking\b",
      r"\bno interest\b")
_rule("WRONG_PERSON", 0.88,
      r"\bwrong (person|email|contact|guy|address)\b", r"\bnot the right (person|contact)\b",
      r"\bno longer (works?|with)\b", r"\bdoesn'?t work here\b", r"\b(i'?m|i am) not the (owner|right)\b",
      r"\bnot my (department|call|decision)\b", r"\bleft the company\b")
# Names must be Capitalised, so these are case-sensitive except for the verb
# ("talk to you soon" is not a referral; "talk to Mike" is).
_rule("REFERRAL", 0.85,
      r"(?i:\b(?:talk|speak) (?:to|with)) [A-Z][a-z]+", r"(?i:\breach out to) [A-Z][a-z]+",
      r"(?i:\bcontact) [A-Z][a-z]+ (?i:at)\b",
      r"\b[A-Z][a-z]+ (?i:handles|runs|manages|is in charge of|takes care of) (?i:that|this|our|marketing|the)\b",
      r"(?i:\b(?:email|e-mail)) [A-Z][a-z]+ (?i:at)\b", r"(?i:\b(?:cc'?ing|copying|looping in|forwarding (?:this|it) to))\b",
      r"(?i:\bthe (?:owner|person you want|best person|right person) is)\b", flags=0)
_rule("READY_TO_BUY", 0.90,
      r"\blet'?s (do it|do this|go|move forward|get started|proceed)\b", r"\bsign (me|us) up\b",
      r"\bsend (me |us )?(the |an )?(invoice|contract|agreement|payment link|paperwork)\b",
      r"\bhow (do|can) (i|we) (pay|sign up|get started|start)\b", r"\bready to (start|go|buy|move forward|get started)\b",
      r"\b(count (me|us) in|(we'?re|i'?m) in)(?!\s+[a-z])\b",r"\bwhere do (i|we) (pay|sign)\b", r"\btake my money\b", r"\bgo ahead and (start|set)",
      r"\b(proposal|quote|price|pricing|terms) (looks|sounds) good\b", r"\bwe accept\b", r"\bapproved?\b.*\bproposal\b")
_rule("MEETING_REQUEST", 0.88,
      r"\b(call|phone|ring) me\b", r"\bgive (me|us) a (call|ring)\b", r"\bcan (you|we) (call|talk|chat|meet|hop on)\b",
      r"\b(hop|jump|get) on a (call|zoom|meeting)\b", r"\b(set up|schedule|book) (a |some )?(call|time|meeting|zoom|demo)\b",
      r"\b(zoom|teams|google meet)\b", r"\bmeet (up|with you|in person)\b", r"\bwhat time (works|is good)\b",
      r"\b(free|available) (on |this |next )?(mon|tues|wednes|thurs|fri|satur|sun)day\b",
      r"\bshow me a demo\b", r"\bcall (me )?(on|at|tomorrow|today|this|next)\b", r"\bmy (cell|number|phone) is\b")
_rule("PRICE_QUESTION", 0.92,
      r"\bhow much\b", r"\bwhat (does|would|will) (it|this|that|something like this) (cost|run|be)\b",
      r"\bwhat (do|would) you charge\b", r"\b(your )?(pricing|prices?|rates?|fees?|costs?)\?",
      r"\bwhat'?s the (cost|price|investment|damage)\b", r"\b(cost|price|pricing) (on|for) (this|that|it)\b",
      r"\bany (pricing|price|cost)\b", r"\bsend (me |us )?(the )?(pricing|prices|rates)\b", r"\bballpark\b",
      r"\bmonthly fee\b.*\?", r"\bis there a (fee|cost|charge)\b")
_rule("MORE_INFORMATION", 0.85,
      r"\btell me more\b", r"\bmore (info|information|details)\b",
      r"\b(email|send|shoot) (me|us|over) (the |some |more |a few )?(details|info|information|specifics)\b",r"\bsend (me |us )?(some |more )?(info|information|details|examples?)\b",
      r"\bhow does (it|this|that) work\b", r"\bwhat exactly\b", r"\bcan you explain\b", r"\bwhat is (this|it)\b",
      r"\bdoes (it|this) (work|integrate) with\b", r"\bdo you (have|offer|work|do|integrate)\b.*\?",
      r"\b(case stud(y|ies)|examples?|references|testimonials)\b.*\?", r"\bwho (are you|is this)\b",
      r"\bwhat (company|business) (is this|are you)\b", r"\bhow (long|fast|quickly) (does|would|will)\b")
_rule("OBJECTION", 0.85,
      r"\btoo (expensive|pricey|much)\b", r"\bout of (our|my) budget\b", r"\bno budget\b", r"\bcan'?t afford\b",
      r"\b(already|currently) (have|use|using|work with|got)\b", r"\bwe (have|use) (someone|a guy|a company|an? (answering|call))\b",
      r"\b(tried|done) (that|this|something like this) before\b", r"\bdoesn'?t work for us\b",
      r"\bneed to (talk|check|run it by|discuss) (with|by)? ?(my|our) (partner|wife|husband|boss|team|business partner)\b",
      r"\b(my|our) (partner|wife|husband|boss) (decides|handles|would need)\b", r"\bnot sure (it|this|that) (would|will) work\b",
      r"\bsounds like a scam\b", r"\bhow do i know\b", r"\bdon'?t trust\b", r"\bwe don'?t miss (many |any )?calls\b")
_rule("NOT_NOW", 0.86,
      r"\bnot (right )?now\b", r"\bmaybe (later|next)\b", r"\b(circle|check|reach|touch) back\b", r"\bnot at this time\b",
      r"\b(next|in a few|in a couple( of)?) (month|months|quarter|year|weeks)\b", r"\bafter (the )?(season|holidays|summer|winter|new year)\b",
      r"\b(busy|slow) season\b", r"\brevisit\b", r"\bin (january|february|march|april|may|june|july|august|september|october|november|december)\b",
      r"\b(too busy|swamped) (right now|at the moment)\b", r"\bdown the road\b", r"\bfollow up (in|next|after)\b")
_rule("INTERESTED", 0.85,
      r"\b(sounds|looks) (interesting|good|great|useful|cool|helpful|promising)\b", r"\b(i'?m|we'?re|i am|we are) interested\b",
      r"\binterested\b", r"\bopen to (it|this|that|hearing|learning|a)\b", r"\bi'?d (like|love) to\b", r"\bwe'?d (like|love) to\b",
      r"\b(could|can) use (this|that|something like this|help)\b", r"\bwe need (this|that|help)\b", r"\byes,? please\b",
      r"\bkeen\b",r"\bworth (a|a quick) (look|chat)\b.*\byes\b")

LEGAL_OR_ANGRY = _p(r"\b(attorney|lawyer|legal action|sue|lawsuit|report (you|this)|ftc|can-?spam|gdpr|ccpa|harass\w*)\b",
                    r"\b(f+u+c+k|shit|scam(mer)?s?|idiot|stupid|pissed)\b", r"\bhow did you get (my|this|our) (email|address|info)\b")
_AFFIRM_WORD = (r"(y(es|eah|ep|up|a)(,? please)?|sure|ok(ay)?|k|sounds good|that works|that'?s fine|works for me|perfect|great|"
                r"absolutely|definitely|for sure|please do|go for it|do it|alright|fine|cool|👍|✅)")
AFFIRMATION = re.compile(rf"^\W*{_AFFIRM_WORD}([\s,.!-]+{_AFFIRM_WORD})*\W*$", re.I)
ASK_ACTIONS = [  # "Want me to <do X>?" in OUR last message -> what a bare "yes" agrees to
    (re.compile(r"\b(send|share|email)\b[^.?!\n]{0,25}\b(pricing|price|prices|rates|cost)\b", re.I), "PRICE_QUESTION",
     "asked whether we should send pricing"),
    (re.compile(r"\b(send|share|email)\b[^.?!\n]{0,30}\b(demo|video|walkthrough|example|info|details|audit|breakdown|mockup|preview)\b", re.I),
     "MORE_INFORMATION", "offered to send more information"),
    (re.compile(r"\b(does|would|will) (\w+day|tomorrow|today)[^?]{0,40}(work|suit)\b|\bcall (on|at) [^?]{2,40}\?", re.I),
     "MEETING_REQUEST", "proposed a specific meeting time"),
]
AUTO_HEADERS = ("auto-submitted", "x-autoreply", "x-autorespond", "x-auto-response-suppress")
AUTO_SUBJECT = re.compile(r"^(auto(matic)?[- ]?(reply|response)|out of (the )?office|ooo\b|away from|on vacation)", re.I)
AUTO_BODY = _p(r"\b(i am|i'?m|i will be) (currently )?(out of (the )?office|away|on vacation|on leave|traveling)\b",
               r"\bthis is an auto(mated|matic)? (reply|response|message)\b", r"\blimited access to (my )?e-?mail\b",
               r"\bwill (respond|reply|get back to you) (when|upon) (i|my) return\b",
               r"\bthank you for (your email|contacting|reaching out)[^.]*\. (we|i) (will|'ll) (respond|get back|be in touch)\b",
               r"\bwe have received your (message|email|inquiry)\b")
BOUNCE_SENDER = re.compile(r"^(mailer-daemon|postmaster|mail-daemon|bounce|bounces)@", re.I)
BOUNCE_SUBJECT = re.compile(r"(delivery status notification|undeliver(able|ed)|mail delivery (failed|subsystem)|"
                            r"returned mail|delivery (has )?failed|address not found|failure notice)", re.I)
PRICE_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?(?:\s?(?:/|per)\s?(?:mo|month|yr|year|week))?", re.I)
TIME_RE = re.compile(r"\b((?:mon|tues|wednes|thurs|fri|satur|sun)day|tomorrow|today|tonight|this (?:week|afternoon|morning|evening)|"
                     r"next (?:week|\w+day)|\d{1,2}(?::\d{2})?\s?(?:am|pm)|noon|morning|afternoon)\b", re.I)
MONTH_RE = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
                      r"next (?:month|quarter|year|spring|summer|fall|winter)|after the \w+)\b", re.I)

PRIORITY = ["UNSUBSCRIBE", "NOT_INTERESTED", "WRONG_PERSON", "REFERRAL", "READY_TO_BUY", "MEETING_REQUEST",
            "PRICE_QUESTION", "OBJECTION", "MORE_INFORMATION", "NOT_NOW", "INTERESTED"]


@dataclass
class Fact:
    fact_type: str
    fact_text: str
    value: dict = field(default_factory=dict)


@dataclass
class Classification:
    label: str
    confidence: float
    evidence: list[str]
    summary: str
    interpretation: str
    recommended_action: str
    needs_review_reason: str | None = None
    secondary: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    legal_or_angry: bool = False
    referral: dict | None = None
    meeting_request: str | None = None
    new_text: str = ""


def _hits(label: str, text: str) -> list[tuple[str, float]]:
    out = []
    for pat, conf in RULES.get(label, []):
        m = pat.search(text)
        if m:
            out.append((m.group(0), conf))
    return out


def _mask(text: str, phrases: list[str]) -> str:
    for ph in phrases:
        text = re.sub(re.escape(ph), " " * len(ph), text, flags=re.I)
    return text


def _sentence(text: str, phrase: str) -> str:
    for s in re.split(r"(?<=[.!?])\s+|\n+", text):
        if phrase.lower() in s.lower():
            return one_line(s, 220)
    return one_line(phrase, 220)


def _is_auto(headers: dict, subject: str, text: str) -> bool:
    h = {k.lower(): str(v).lower() for k, v in (headers or {}).items()}
    if h.get("auto-submitted", "no") not in ("", "no"):
        return True
    if any(k in h for k in AUTO_HEADERS[1:]) or h.get("precedence", "") in ("auto_reply", "auto-reply"):
        return True
    return bool(AUTO_SUBJECT.search(subject or "")) or any(p.search(text) for p in AUTO_BODY)


def _last_outbound(thread: list[dict]) -> dict | None:
    outs = [m for m in thread if m.get("direction") == "outbound"]
    return outs[-1] if outs else None


def _summary(thread: list[dict], state: dict | None, facts: list[dict]) -> str:
    """One paragraph a human can read: what we pitched, what they said so far."""
    outs = [m for m in thread if m.get("direction") == "outbound"]
    ins = [m for m in thread if m.get("direction") == "inbound"]
    parts = []
    if outs:
        first = outs[0]
        parts.append(f"We first emailed on {str(first.get('occurred_at'))[:10]} (\"{one_line(first.get('subject') or '', 80)}\""
                     f"{', offer: ' + first['offer'] if first.get('offer') else ''}).")
        if len(outs) > 1:
            parts.append(f"{len(outs)} emails sent in total; the latest was a {outs[-1].get('kind', 'message')}.")
    if len(ins) > 1:
        prior = ins[-2]
        parts.append(f"Their previous reply: \"{one_line(strip_quoted(prior.get('body') or ''), 140)}\".")
    prices = [f["fact_text"] for f in facts if f.get("fact_type") in ("price_discussed", "price_objection")]
    if prices:
        parts.append("Prices already discussed: " + "; ".join(prices[-3:]) + ".")
    if state and state.get("current_status"):
        parts.append(f"Status before this reply: {state['current_status']}.")
    return " ".join(parts) or "No earlier messages on record for this company."


def classify(inbound: dict, thread: list[dict], state: dict | None = None, facts: list[dict] | None = None,
             our_addresses: set[str] | None = None) -> Classification:
    """Classify the newest inbound message.

    inbound: {subject, body, sender, headers}
    thread:  every earlier message for this company, oldest first (outbound + inbound)
    """
    facts = facts or []
    our_addresses = {a.lower() for a in (our_addresses or set())}
    subject = inbound.get("subject") or ""
    sender = addr(inbound.get("sender") or "")
    body = inbound.get("body") or ""
    text = strip_quoted(body)
    summary = _summary(thread, state, facts)
    last_out = _last_outbound(thread)

    def result(label, conf, evidence, interp, action, reason=None, **kw) -> Classification:
        if label not in LABELS:
            raise ValueError(label)
        return Classification(label=label, confidence=round(conf, 3), evidence=evidence, summary=summary,
                              interpretation=interp, recommended_action=action, needs_review_reason=reason,
                              new_text=text, **kw)

    # 1) machines first
    if BOUNCE_SENDER.search(sender) or BOUNCE_SUBJECT.search(subject):
        return result("DELIVERY_FAILURE", 0.97, [one_line(subject, 160)],
                      "The email did not reach them (bounce).", "Stop emailing this address; find another contact if valuable.")
    if _is_auto(inbound.get("headers") or {}, subject, text):
        back = MONTH_RE.search(text) or TIME_RE.search(text)
        return result("AUTO_REPLY", 0.93, [one_line(subject, 120) or one_line(text, 120)],
                      "Automatic out-of-office / receipt message; no human has answered yet.",
                      "No action. Keep the conversation as it was." + (f" They mention being back: {back.group(0)}." if back else ""))
    if not text.strip():
        return result("NEEDS_REVIEW", 0.0, [], "The reply has no new text we could read (maybe only an attachment or image).",
                      "Open the email and read it yourself.", reason="empty_or_unreadable")

    legal = [m.group(0) for p in LEGAL_OR_ANGRY for m in [p.search(text)] if m]
    hits: dict[str, list[tuple[str, float]]] = {}
    # "don't call me" is a preference, never a meeting request
    masked = re.sub(r"(?i)\b(don'?t|do not|no need to|please don'?t) (call|phone|ring)( me| us)?\b",
                    lambda m: " " * len(m.group(0)), text)
    # Negative phrases are found first and blanked out, so "not interested" can't
    # also count as "interested", and "don't need this" can't count as "need this".
    for label in ("UNSUBSCRIBE", "NOT_INTERESTED"):
        h = _hits(label, masked)
        if h:
            hits[label] = h
            masked = _mask(masked, [p for p, _ in h])
    for label in PRIORITY:
        if label in hits:
            continue
        h = _hits(label, masked)
        if h:
            hits[label] = h

    other_emails = sorted({e.lower() for e in EMAIL_RE.findall(text)} - {sender} - our_addresses)
    extracted = _extract_facts(text, hits, other_emails)
    meeting = ", ".join(sorted({m.group(0) for m in TIME_RE.finditer(text)})) or None

    # 2) short "yes" - only meaningful against ONE specific question we asked
    if AFFIRMATION.match(text.strip()):
        return _affirmation(text, last_out, facts, result, extracted)

    if not hits:
        return result("NEEDS_REVIEW", 0.3, [one_line(text, 200)], "No clear intent found in the reply.",
                      "Read it and decide; nothing is sent automatically.", reason="no_recognised_intent",
                      facts=extracted, legal_or_angry=bool(legal))

    labels = [l for l in PRIORITY if l in hits]
    evidence = [_sentence(text, p) for l in labels for p, _ in hits[l]][:6]
    positives = [l for l in labels if l in POSITIVE]

    if "UNSUBSCRIBE" in hits:
        if positives and not legal:
            return result("NEEDS_REVIEW", 0.5, evidence, "They used opt-out wording but also sounded positive - contradictory.",
                          "Read it. All sending to them is paused until you decide.", reason="conflict_unsubscribe_and_positive",
                          secondary=labels, facts=extracted, legal_or_angry=bool(legal))
        return result("UNSUBSCRIBE", max(c for _, c in hits["UNSUBSCRIBE"]), evidence,
                      "They asked not to be emailed again." + (" The message is angry or mentions legal action." if legal else ""),
                      "Do not contact again. Already suppressed; nothing else to send.",
                      secondary=labels[1:], facts=extracted, legal_or_angry=bool(legal))
    if "NOT_INTERESTED" in hits and positives:
        return result("NEEDS_REVIEW", 0.45, evidence, "The reply mixes a no with a positive signal.",
                      "Read it and decide; follow-ups are paused.", reason="conflict_negative_and_positive",
                      secondary=labels, facts=extracted, legal_or_angry=bool(legal))
    if legal and "NOT_INTERESTED" not in hits:
        return result("NEEDS_REVIEW", 0.4, evidence + legal[:2], "The message is angry or mentions legal/privacy concerns.",
                      "Handle personally. All sending to them is paused.", reason="legal_or_angry",
                      secondary=labels, facts=extracted, legal_or_angry=True)

    primary = labels[0]
    if primary == "WRONG_PERSON" and ("REFERRAL" in hits or other_emails):
        primary = "REFERRAL"
    if primary == "REFERRAL" and not other_emails and not _referral_name(text):
        return result("NEEDS_REVIEW", 0.5, evidence, "They seem to point to someone else, but no name or email was clear.",
                      "Read it and note who to contact.", reason="referral_unclear", secondary=labels, facts=extracted)
    if primary == "OBJECTION" and positives:
        # "Sounds interesting but $1,500 is too much" - objection with interest: still objection, keep interest
        pass
    conf = max(c for _, c in hits[primary])
    referral = None
    if primary == "REFERRAL":
        referral = {"emails": other_emails, "name": _referral_name(text)}
    interp, action = _meaning(primary, labels, text, state, facts, referral, meeting)
    return result(primary, conf, evidence, interp, action, secondary=[l for l in labels if l != primary],
                  facts=extracted, referral=referral, meeting_request=meeting if primary == "MEETING_REQUEST" else None,
                  legal_or_angry=bool(legal))


def _affirmation(text, last_out, facts, result, extracted) -> Classification:
    if last_out is None:
        return result("NEEDS_REVIEW", 0.2, [text], "A one-word 'yes' with no email of ours on record to answer.",
                      "Read the thread and decide.", reason="affirmation_without_context", facts=extracted)
    ours = strip_quoted(last_out.get("body") or "")
    if last_out.get("price_quoted") or last_out.get("kind") in ("pricing", "proposal"):
        price = last_out.get("price_quoted") or "the price in our last email"
        return result("READY_TO_BUY", 0.75, [text, f"Our last email ({last_out.get('kind')}) quoted {price}"],
                      f"They said \"{text.strip()}\" to our {last_out.get('kind')} that quoted {price}. Most likely agreeing to it.",
                      "Confirm scope and send the payment/onboarding step - you approve first.",
                      facts=extracted + [Fact("commitment", f"Said \"{text.strip()}\" to {price}", {"price": price})])
    if last_out.get("cta") and "reply start" in last_out["cta"].lower() and text.strip().lower().strip(".!") == "start":
        return result("READY_TO_BUY", 0.8, [text, f"Our CTA: {last_out['cta']}"], "They replied START as our email asked.",
                      "Send the onboarding/payment step - you approve first.", facts=extracted)
    cta = last_out.get("cta") or ""
    questions = [q for q in re.findall(r"[^.?!\n]*\?", ours)]
    candidates = [(lbl, why) for pat, lbl, why in ASK_ACTIONS if pat.search(cta or " ".join(questions))]
    if cta and len(candidates) == 1 and len(questions) <= 1:
        lbl, why = candidates[0]
        return result(lbl if lbl != "PRICE_QUESTION" else "INTERESTED", 0.75, [text, f"Our question: {one_line(cta, 160)}"],
                      f"They said \"{text.strip()}\" to our one question ({why}).",
                      {"MORE_INFORMATION": "Send what we offered (draft ready for approval).",
                       "MEETING_REQUEST": "Confirm the time we proposed.",
                       "PRICE_QUESTION": "Send the pricing we offered (draft ready for approval)."}[lbl], facts=extracted)
    return result("NEEDS_REVIEW", 0.3, [text, f"Our last email asked {len(questions)} question(s)"],
                  f"They said \"{text.strip()}\" but our last email didn't ask one clear yes/no question, "
                  "so we can't tell what they are agreeing to.",
                  "Read the thread and reply personally. Nothing is sent automatically.",
                  reason="affirmation_ambiguous", facts=extracted)


def _referral_name(text: str) -> str | None:
    m = re.search(r"(?i:\b(?:talk|speak) (?:to|with)) ([A-Z][a-z]+(?: [A-Z][a-z]+)?)|(?i:\breach out to) ([A-Z][a-z]+(?: [A-Z][a-z]+)?)|"
                  r"(?i:\bcontact) ([A-Z][a-z]+(?: [A-Z][a-z]+)?)|\b([A-Z][a-z]+) (?i:handles|runs|manages|is in charge of|takes care of)|"
                  r"(?i:\bthe (?:owner|right person|best person) is) ([A-Z][a-z]+(?: [A-Z][a-z]+)?)", text)
    if not m:
        return None
    name = next(g for g in m.groups() if g)
    return None if name.lower() in {"me", "us", "him", "her", "them", "the", "our"} else name


def _extract_facts(text: str, hits: dict, other_emails: list[str]) -> list[Fact]:
    facts: list[Fact] = []
    low = text.lower()
    for price in PRICE_RE.findall(text):
        kind = "price_objection" if "OBJECTION" in hits and re.search(r"too (expensive|much|pricey)|budget|afford", low) else "price_discussed"
        facts.append(Fact(kind, f"Prospect mentioned {price.strip()}" + (" as too expensive" if kind == "price_objection" else ""),
                          {"amount_text": price.strip()}))
    if "OBJECTION" in hits and not any(f.fact_type == "price_objection" for f in facts):
        for p, _ in hits["OBJECTION"]:
            ftype = "price_objection" if re.search(r"expensive|pricey|budget|afford|too much", p, re.I) else "objection"
            facts.append(Fact(ftype, _sentence(text, p)))
    if re.search(r"\b(partner|wife|husband|boss|business partner)\b", low) and re.search(r"\b(talk|check|run it by|discuss|approv|decid)", low):
        facts.append(Fact("decision_maker", _sentence(text, re.search(r"partner|wife|husband|boss", low).group(0))))
    if "NOT_NOW" in hits:
        when = MONTH_RE.search(text)
        facts.append(Fact("timing", "Wants to revisit " + (when.group(0) if when else "later"), {"when": when.group(0) if when else None}))
    if re.search(r"\b(by|via|over) (email|e-mail|text)\b|\bdon'?t call\b|\bemail (is|works) best\b|\bprefer (email|text)\b", low):
        m = re.search(r"[^.?!\n]*(email|e-mail|text|call)[^.?!\n]*", text, re.I)
        facts.append(Fact("preference", one_line(m.group(0) if m else text, 200)))
    m = re.search(r"\b(?:only|just) (?:want|need|interested in) ([^.?!\n]{3,80})", text, re.I)
    if m:
        facts.append(Fact("service_interest", f"Only wants {m.group(1).strip()}"))
    m = re.search(r"\b(?:not|no|don'?t want)(?: a| any)? (website (?:rebuild|redesign)|new website|ads|advertising)\b", text, re.I)
    if m:
        facts.append(Fact("scope_exclusion", f"Does not want {m.group(1)}"))
    for q in re.findall(r"[^.?!\n]*\?", text)[:3]:
        facts.append(Fact("question", one_line(q.strip(), 200)))
    for e in other_emails:
        facts.append(Fact("referral", f"Referred to {e}", {"email": e, "name": _referral_name(text)}))
    if "MEETING_REQUEST" in hits:
        facts.append(Fact("meeting_request", one_line(_sentence(text, hits["MEETING_REQUEST"][0][0]), 200),
                          {"when": ", ".join(sorted({m.group(0) for m in TIME_RE.finditer(text)})) or None}))
    return facts


def _meaning(label, labels, text, state, facts, referral, meeting) -> tuple[str, str]:
    prev_price = next((f["fact_text"] for f in reversed(facts) if f.get("fact_type") == "price_discussed"), None)
    also_interested = "INTERESTED" in labels and label != "INTERESTED"
    m = {
        "INTERESTED": ("They are positive about the offer and have not asked for anything specific yet.",
                       "Send a short personal reply plus the mini audit (drafts ready for your approval)."),
        "PRICE_QUESTION": ("They are asking what it costs" + (" and sound interested" if also_interested else "") +
                           " - they have not rejected the offer." + (f" Earlier: {prev_price}." if prev_price else ""),
                           "Send the pricing reply (draft ready for your approval)."),
        "MORE_INFORMATION": ("They want to understand the offer better before deciding.",
                             "Answer their question using the offer details (draft ready for your approval)."),
        "MEETING_REQUEST": ("They want to talk live" + (f" - they mentioned: {meeting}" if meeting else "") + ".",
                            "Coordinate the call time with them yourself."),
        "READY_TO_BUY": ("They are signalling they want to go ahead.",
                         "Move to proposal/payment/onboarding - proposal draft is ready for your approval."),
        "OBJECTION": ("They raised a concern" + (" but still sound interested" if also_interested else "") + ".",
                      "Answer the specific concern (draft ready for your approval)."),
        "NOT_NOW": ("Timing is wrong for them right now; not a no.",
                    "Stop the cold sequence and follow up at the date they gave."),
        "REFERRAL": (f"They pointed us to {', '.join(referral['emails']) if referral and referral['emails'] else (referral or {}).get('name')}.",
                     "Contact the referred person, mentioning who referred you (draft ready for approval)."),
        "WRONG_PERSON": ("This contact is not the right person, and they didn't say who is.",
                         "Stop emailing this address; find the owner another way."),
        "NOT_INTERESTED": ("They declined.", "Stop all follow-ups. No reply needed."),
    }
    return m[label]
