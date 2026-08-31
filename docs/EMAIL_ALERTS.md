# Important email → Discord

## What this does

Every two hours during the day, a machine on the internet logs into the
mailbox, reads anything that arrived since the last check, and sends a Discord
message **only** for mail that actually matters — money moving, a security
alert, coursework with a deadline, a real reply about a job, or one of the
other automations breaking.

Everything else — newsletters, sales, receipts, "welcome to…" mail, login
codes, and the digests this system emails to itself — is dropped on purpose.

The laptop is not involved. It can be off.

## Where each piece runs

| Step | Runs on | Proof it is not the laptop |
|---|---|---|
| The schedule | GitHub Actions | Runs are listed under the repo's Actions tab with a GitHub-owned runner |
| Reading the mailbox | GitHub's Ubuntu runner | Same run log; the job prints its runner name and hostname |
| Deciding what matters | Same runner | `src/cloudos/email_alerts/filter.py` |
| Sending to Discord | The Cloudflare Worker | `cloudos-ingress`, already deployed, cron every 30 min |

The Discord webhook URL is stored **only** in the Cloudflare Worker. GitHub
never sees it; the scheduled job calls the Worker's authenticated `/notify`
route instead, so there is one copy of that secret and one place to rotate it.

## The one credential a human must supply

The mailbox password. Two repository secrets on `mattumali579/cloud-ai-os`:

| Secret | What it is |
|---|---|
| `EMAIL_ADDRESS` | The mailbox to watch, e.g. `you@gmail.com` |
| `EMAIL_APP_PASSWORD` | A Gmail **app password**, not the normal password |

Optional:

| Secret | Default | Use it when |
|---|---|---|
| `EMAIL_IMAP_HOST` | `imap.gmail.com` | The mailbox is not Gmail |
| `EMAIL_OWNER_ALIASES` | *(empty)* | Other addresses that are also "you", comma separated |

Gmail requires 2-Step Verification to be on before it will issue an app
password. Create one at <https://myaccount.google.com/apppasswords>, then:

```sh
gh secret set EMAIL_ADDRESS      --repo mattumali579/cloud-ai-os
gh secret set EMAIL_APP_PASSWORD --repo mattumali579/cloud-ai-os
```

### A school mailbox on Microsoft 365

Microsoft turned off password-based IMAP for Microsoft 365 accounts, so a
university Outlook mailbox **cannot** be read with an app password. The
supported way to cover it is a forwarding rule in Outlook that sends mail to
the Gmail address above; the filter already scores `.edu` senders higher.

## Testing it

```sh
# Prove the cloud runtime, the filter and Discord delivery, no mailbox needed:
gh workflow run email-alerts.yml --repo mattumali579/cloud-ai-os -f mode=self-test

# Classify real mail and print the verdicts without sending anything:
gh workflow run email-alerts.yml --repo mattumali579/cloud-ai-os -f mode=dry-run

# Normal run:
gh workflow run email-alerts.yml --repo mattumali579/cloud-ai-os
```

## Why every two hours and not every 15 minutes

GitHub Free includes 2,000 Actions minutes a month on private repos and rounds
every job **up** to a whole minute. A 30-minute schedule would spend about
1,488 of those minutes on this one job and eventually exhaust the account —
at which point *every* scheduled workflow stops without warning. Nine runs a
day costs roughly 279 minutes a month.

The cadence is also a deliberate attention choice: important mail is worth an
interruption, a newsletter is not, and a channel that buzzes constantly gets
muted and stops working as an alert.

## Tuning what counts as important

Everything lives in `src/cloudos/email_alerts/filter.py`:

- `CATEGORY_WEIGHT` — how much each kind of mail is worth
- `NOTIFY_THRESHOLD` — the score needed to send (currently 50)
- `STRONG_CATEGORIES` — categories that bulk-mail headers may not veto,
  because banks and CI systems both send in bulk

`tests/test_email_alerts_filter.py` pins the behaviour against 20 real
messages from the actual inbox. Change a weight, run `pytest`, and the test
will tell you if a newsletter just became a notification.

## When something breaks

- The mailbox cannot be read → the run **fails loudly** and posts a Discord
  message saying the watcher itself broke. A silent watcher is the failure
  mode this design refuses.
- Discord rejects a message → counted as `failed` in the run summary.
- The same email is never sent twice: `state/email_alerts_seen.json` is
  committed back after each run and holds 14 days of message IDs.
