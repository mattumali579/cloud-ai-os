# Missed-Call Text-Back + Estimate Follow-Up

BrightReach Media sells this to HVAC, plumbing, roofing, electrical, and similar local shops.

**$1,500 once. The shop owns the install. 30 days, it works or the money comes back.**

## What the shop gets

- An unanswered call becomes a text from that business within the time it takes Twilio to hit the webhook. The text uses their name, hours, and booking link, and it tells the caller they can reply STOP.
- The caller can text back. The thread asks what they need, the address or ZIP, and whether it is today, this week, or flexible. No paid AI.
- The owner gets that lead on their phone (SMS) and, if configured, by email or chat webhook. The owner can answer from the dashboard or by texting `REPLY +1... message` from their own phone.
- The owner logs an estimate (form or text). The customer gets a short follow-up the same day, two days later, and five days later. A reply, a won/lost mark, or STOP ends the sequence.
- STOP, HELP, and START are honored. Opted-out numbers are not texted again. Follow-ups wait out quiet hours. Each shop has a daily text cap.
- One running copy serves many shops. Each shop has its own number, wording, owner, and a private results page: missed calls, callers who replied, leads, estimates, and jobs marked won. That page is what you show for the guarantee.
- A browser demo for sales calls. It does not need Twilio and it does not send a text.

## Run the demo

Open `demo/index.html` in a browser (double-click, or any static host). GitHub Pages steps are in `RUNBOOK.md`.

Click **Miss the call**. Reply as the caller, or use the suggestion chips. The owner’s phone and the work order fill in from the same rules the live workflows use.

Nothing on that page calls Twilio.

## Run the tests

```bash
cd products/missed-call-textback
npm test
```

Node 22 and local Postgres admin access (`sudo -u postgres`) are what the suite uses here. The suite:

- checks the qualification engine (missed call, STOP/HELP/START, quiet hours, rate limit, follow-up dates, two-shop isolation of the pure logic)
- checks every workflow file is importable JSON, embeds that same engine, and contains no live Twilio secret
- applies `sql/001_schema.sql` to a throwaway database `mctb_test`
- posts simulated Twilio voice and SMS webhooks through the workflow graph and reads the rows back

`npm test` writes a plain-language transcript to `/opt/cursor/artifacts/missed-call-e2e.txt` when that folder exists.

Rebuild the workflow JSON after any edit to `logic/engine.js`:

```bash
npm run build
```

## Install for a paying client

Follow `RUNBOOK.md`. Short version: Postgres schema `mctb`, import the six files in `workflows/`, point the shop’s unanswered calls at a Twilio number, set four environment variables, open the dashboard link the setup script prints.

No Twilio account is required to develop or to show the demo. Real texting starts only when you put that client’s (or your) Twilio credentials in the environment. This repo does not contain those credentials and does not buy numbers.

## Layout

| Path | Role |
|---|---|
| `workflows/*.json` | Import these into n8n |
| `logic/engine.js` | Wording, STOP rules, quiet hours, follow-up dates. The workflows embed this file. |
| `sql/001_schema.sql` | Postgres schema `mctb` only. It does not touch outreach tables. |
| `demo/` | Static sales page |
| `scripts/setup_tenant.sh` | One command to add a shop |
| `scripts/install_into_n8n.sh` | Apply schema and import workflows |
| `docker-compose.yml` | Optional dedicated Postgres + n8n if you do not want to use the existing Cloud AI OS stack |

## Limits worth knowing before the first install

- US numbering. Ten-digit numbers become `+1`. Already-international `+` numbers of 10–15 digits are kept.
- The missed-call text and the replies ride back on Twilio’s own webhook response, so they go out as soon as Twilio reaches n8n. Estimate follow-ups and texts typed into the dashboard go out on a one-minute pass, and only those need the Twilio REST credential.
- Follow-ups are quiet from 9:00 p.m. to 8:00 a.m. in the shop’s time zone. The missed-call text does not wait, because the person just called. A shop can turn that wait on.
- Carrier registration (A2P 10DLC) is required before US local numbers deliver reliably. That approval is outside this repo and can take days. Details and current published prices are in the runbook.
- The dashboard password is a long random link, not a user account system. Treat the link like a password.
- n8n will not import the Postgres password from git. After import, attach a credential named `BrightReach Postgres` to the Postgres nodes (the install script can do this when you give it an n8n login).
