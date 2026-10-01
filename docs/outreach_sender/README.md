# Hostinger outreach sender

Sends the prospect emails from the Hostinger mailbox, follows up, stops on any
reply, and reports. Runs in GitHub Actions every 10 minutes (Ticker chain), so
the laptop can be off.

- Code: `src/cloudos/outreach/`, command: `outreach_sender.py`
- Settings: `config/outreach_sender.yaml` (pace, daily limit, follow-up days, inbox roles)
- Tables: `db/migrations/009_outreach_sender.sql` (`outreach_queue`, `companies.ready_at`, Airtable bookkeeping)
- Schedule: `.github/workflows/outreach-send.yml`; tests: `.github/workflows/outreach-tests.yml`

## Flow

Ready company (lead engine) -> send guard (dedupe on company / domain / email, suppression,
replied, bounced) -> copy + QA (no technology words, postal address, opt-out) -> `outreach_queue`
step 0 -> claimed by one worker -> guard again -> Message-ID saved -> Hostinger SMTP ->
accepted = recorded in `outreach_messages` + company `contacted` + follow-up 1 scheduled
(3 business days), follow-up 2 (5 more) -> Hostinger IMAP replies -> reply layer classifies
-> follow-ups cancelled on any human reply, unsubscribe or bounce -> AgentMail + Airtable.

## Secrets (GitHub Actions, never in git)

| Secret | What |
|---|---|
| `HOSTINGER_EMAIL` | `matt@fitnesshubb.com` |
| `HOSTINGER_EMAIL_PASSWORD` | that mailbox's password (hPanel -> Emails -> the mailbox) |
| `HOSTINGER_DAILY_LIMIT` | optional; the plan's per-mailbox daily send cap (default 100 = trial/free); a lower value lowers the daily limit, a higher one never raises it past `pacing.daily_limit` |
| `SENDER_POSTAL_ADDRESS` | mailing address printed in every email (US CAN-SPAM) |
| `AGENTMAIL_API_KEY` | AgentMail console -> API keys |
| `OWNER_NOTIFY_EMAIL` | optional; where role mail is delivered (default: the manager inbox) |

Without the Hostinger password the job still prepares and checks every email and
sends none. It starts sending on the first run after the password is saved: first
a one-time self-test email, then prospects.

## START / STOP / STATUS

```
gh workflow run outreach-send.yml -R mattumali579/cloud-ai-os -f mode=cycle
gh workflow disable outreach-send.yml -R mattumali579/cloud-ai-os
gh workflow run outreach-send.yml -R mattumali579/cloud-ai-os -f mode=status
python outreach_sender.py status        # locally, with DATABASE_URL
```

## Guarantees and where they are proven

`tests/test_outreach_sender.py` (real Postgres) and `tests/test_outreach_transport.py`
(real local SMTP server): one row per company+step; concurrent workers never share a row;
crash mid-send is never resent (Sent folder decides, else `ambiguous`); interrupted
hand-over is `ambiguous`, not retried; hard rejection suppresses the address for good;
bad login sends nothing and loses nothing; genuine reply / "stop" cancels follow-ups;
out-of-office does not; daily limit (100 from the first sending day, no warm-up ramp) + provider cap + send window + gap between sends; Ready (not discovered)
counts toward 300; AgentMail notices once per event; Airtable writes only changes, 10
per call, within the monthly budget.
