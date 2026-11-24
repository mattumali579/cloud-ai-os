# Security changelog

Public summary only. This repository is public, so detailed audit notes are
kept off GitHub (`docs/security/*.local.md` is git-ignored).

## 2026-11-23 - website/infrastructure hardening (Grok Bot, task GB-1)

- **Local Docker services are loopback-only.** Postgres (5432), Agent API
  (8080), n8n (5679), research engine (5055) and the Maps scraper (8090) were
  published on all interfaces, which made them reachable from the Wi-Fi LAN.
  `infra/docker/docker-compose.yml` now binds them to `127.0.0.1` and `[::1]`.
  Remote phone access stays available through the tailnet-only HTTPS proxy.
- **Secret hygiene.** A webhook token in `products/missed-call-textback/INSTALL_STATUS.md`
  was replaced by a placeholder (the workflow it belonged to is not active).
  `.gitleaks.toml` defined no detection rules (an allowlist only), so any scan
  using it reported "no leaks" by construction; it now extends gitleaks' default
  rules and no longer allow-lists `.env`. Two reviewed historical findings are
  recorded in `.gitleaksignore`.
- **Monitoring.** New `.github/workflows/site-monitor.yml`: hourly uptime,
  auth-denial and TLS-expiry checks (with a built-in self-test proving that a
  broken endpoint fails the run); weekly gitleaks history scan and pip-audit.
- **Dependencies.** `.github/dependabot.yml` enables weekly, rate-limited
  Dependabot version updates for pip and GitHub Actions.

## 2026-11-23 - n8n audit and cleanup (Grok Bot, task GB-2)

- **Backed up before changes.** Backups include every workflow, an encrypted
  credential export, and the full n8n data volume, which holds the encryption-key
  file and the execution history. A restore into a scratch, loopback-only n8n
  container was verified: 16/16 workflows were present and 2/2 credentials
  decrypted. The scratch container was removed afterwards.
- **16 workflows were reduced to 2 active.** Kept: Intelligence Library
  Collector and Jarvis Error Reporter. Disabled: Lead Discovery, ONLINE_MONEY_RESEARCH
  and Sales Optimizer, because their output was unused or stuck. Deleted: 11
  inactive or test workflows. See `n8n/live/README.md`.
- **n8n 2.41.6 → 2.48.8, pinned.** The upgrade was tested on a restored copy
  first. A pre-upgrade volume backup and a local rollback image tag are kept.
- **Least privilege.** The n8n container no longer loads the repo `.env`, which
  holds GitHub, SMTP, Twilio and DB secrets. Container env went from 100
  variables to 24, none of them secrets. `$env` access from nodes is blocked.
  Ports stay loopback-only.
- **Collector relevance gate.** About 95% of collector rows were off-topic. The
  first gated production runs produced 23/23 and 1/1 on-topic rows, in an
  unchanged file format.
- **Failure path tested.** A temporary failing workflow triggered the error
  reporter, which wrote an alert file. The Muse monitor gateway reported the
  failed execution. The probe workflow was then deleted.
