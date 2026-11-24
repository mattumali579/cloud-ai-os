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
