# Outreach Execution Rules

Status: CANONICAL
Owner: Matt Umali
Last updated: 2026-09-22

## Default authorization

When the owner explicitly assigns outreach work, that assignment is standing authorization to execute the send once sender readiness is verified.

Do not stop to ask for a second approval merely because the next action is "send."

Examples that count as authorization:
- "Send these emails."
- "Do outreach to these leads."
- "Contact these gyms."
- "Run this campaign."
- "Send the follow-ups."
- "Do that tonight, right now."

## Required pre-send checks

Before sending, verify:
- the connected sending account is the intended sender;
- the sender account/domain is usable and not obviously misconfigured;
- the recipient/contact data is real enough to avoid knowingly sending to fabricated addresses;
- the send does not violate a platform-enforced security or authorization requirement.

If those checks pass, send without asking again.

## Stop conditions

Pause only when one of these is true:
- login, CAPTCHA, MFA, OAuth, or another platform-required human security step is blocking execution;
- required sending credentials or sender access are missing;
- sender readiness is materially unsafe or unverified;
- the user explicitly asks for a draft only, preview only, or asks not to send;
- a legal/safety/platform restriction blocks the action.

## Reporting rule

Drafted is not sent.
Queued is not sent.
Skipped is not sent.
Only report "sent" when the send action actually succeeds and there is tool evidence.

After sending, report concrete evidence such as:
- number successfully sent;
- failed count;
- skipped count and reason;
- provider/tool confirmation when available.

## Email Marketer role override

The prior rule "never sends anything without explicit approval" is replaced by:
"An explicit outreach assignment from the owner is the approval. Verify sender readiness, then execute without asking again."
