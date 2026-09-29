# BrightReach reply layer

What it does, in one line: every 10 minutes it reads the outreach Gmail, remembers
every email we sent and every reply, works out what each reply means using the whole
conversation, updates that company's status, tells you on Discord when something
matters, and puts a ready-to-send reply in your Gmail Drafts. **It never emails a
prospect by itself.** You press send.

- Code: `src/cloudos/conversations/`
- Commands: `outreach_status.py`, `outreach_replies.py`
- Database: `db/migrations/007_conversation_memory.sql` (same Supabase as the lead engine)
- Schedule: `.github/workflows/outreach-replies.yml` (every 10 minutes, in the cloud)
- Prices it may quote: `config/brightreach_offers.yaml` (copied from the offer one-pager)

## Daily use

```
python outreach_status.py            # today, top prospects, what needs you
python outreach_status.py --queue    # only what needs you
python outreach_status.py --company "ABC Roofing"   # the whole conversation + what they told us
```

After you've looked at something that needed you:

```
python outreach_replies.py resolve <company_id> interested "read it - they want pricing"
python outreach_replies.py won <company_id> --setup 1500 --monthly 400
python outreach_replies.py lost <company_id> "went with someone else"
```

## For any sender (Codex, scripts, anything)

Right before sending ANY email to a prospect:

```
python outreach_replies.py check john@abc.com followup     # exit 0 = allowed, 3 = do NOT send
```

Exit 3 means "do not send" for every reason, including the check itself failing
(database down, bad input). Only an explicit exit 0 is permission.

or straight from SQL: `SELECT outreach_send_check('john@abc.com', 'followup');`

It blocks: unsubscribed / do-not-contact, bounced addresses, anyone who has replied
(for generic follow-ups), anyone waiting for your review, closed deals, and a second
first-touch to a company we already emailed. Addresses match however they are written
(`John.Smith+promo@googlemail.com` = `johnsmith@gmail.com`).

Built in, fail-closed (no database = no send):
- the SMTP outbox (`/v1/email/send`) checks every prospect recipient before connecting and
  again right before each email; the owner's own addresses are exempt;
- the lead engine only hands a company to the sender's Airtable tray if the guard clears
  it as a first touch.

Right after a send the provider confirmed (Gmail accepted it and gave a Message-ID):

```
python outreach_replies.py confirm-send --company <id> --to john@abc.com --subject "..." \
    --body-file body.txt --message-id "<...>" --thread <gmail thread id> --kind cold
```

That saves the exact email, its IDs, and only then writes Airtable "Emailed At".
Queued, drafted or attempted is never recorded as sent.

## How a reply is handled

1. Match it to a company: Gmail thread → In-Reply-To/References → exact address we
   emailed → known contact. Website domain alone is "not sure" and goes to review.
2. Load everything: what we sent, every earlier reply, prices already discussed,
   what they told us before, current status.
3. Classify it. Unclear, contradictory, a bare "yes" we can't tie to one question,
   or angry/legal wording → NEEDS_REVIEW. Nothing automatic happens on those.
4. Update the one status for that company (only allowed moves; never backwards).
5. Remember the facts (price objections, "revisit in November", "talk to my partner",
   referrals, what they do/don't want).
6. Prepare a draft (pricing, reply + audit, proposal, referral intro) → Gmail Drafts.
7. Ping you on Discord once, only when it matters.

## Recovery

- Re-read the mailbox from a date (safe, never duplicates): `python outreach_replies.py poll --rescan --since 01-Sep-2026`
- Record without pinging or drafting: `python outreach_replies.py poll --quiet`
- Stop it: `gh workflow disable outreach-replies.yml -R mattumali579/cloud-ai-os`
- Tests: `python -m pytest tests/test_conversations_classify.py` and, with a throwaway
  Postgres, `CONVERSATIONS_TEST_DATABASE_URL=... python -m pytest tests/test_conversations.py`
