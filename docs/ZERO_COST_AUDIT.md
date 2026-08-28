# Zero-cost audit (addendum §25)

Anything capable of creating an automatic charge is FAIL unless explicitly approved
by the owner. Re-run this audit before declaring V1 complete and after ANY provider
change.

| Service | Free tier used | Billing enabled? | Automatic overage possible? | Fail-closed mechanism | Expected usage | Quota | Headroom | Verdict |
|---|---|---|---|---|---|---|---|---|
| Oracle Cloud (A1) | Always Free Ampere A1 — conservative assumption 2 OCPU / 12 GB shared | Owner must keep account on Always Free (NOT trial→PAYG upgrade) | No, if account stays Always Free; instance creation fails rather than bills | Design assumes reclamation; recovery via bootstrap.sh; no auto-provisioning of paid shapes anywhere in code | 1 VM, 2 OCPU / 8–12 GB | Always Free allocation | none needed — fixed | PASS if account is Always Free tier |
| Supabase | Free project, 500 MB DB | No card required on free | No — free projects pause, they don't bill | Retention pruning (configurable days), compact logging (hashes not payloads), SUPABASE_DB_BUDGET_MB=500 documented monitor | <100 MB steady state | 500 MB hard budget | >400 MB | PASS |
| Cloudflare Workers | Free plan (100k req/day, 10ms CPU) | Free plan cannot overage-bill | No — requests are dropped at limit | Stateless worker, trivial CPU; health cron 96 runs/day | «1k req/day | 100k/day | ~99% | PASS |
| Cloudflare Workers AI | Free daily allocation (neurons) | Only if owner upgrades — code never does | On paid plans yes; on free, requests are rejected | FREE_MODEL_ALLOWLIST + WORKERS_AI_DAILY_BUDGET counter → QUOTA_EXHAUSTED (block task, notify) | bounded by budget env | 10k neurons/day class | budget set at 9000 | PASS |
| Gemini API (free) | Free tier keys | No billing account attached ⇒ hard-rejected at limit | No, without a billing account | GEMINI_DAILY_REQUEST_BUDGET counter → QUOTA_EXHAUSTED; privacy gate limits what's sent at all | bounded by budget env | provider RPD limits | budget conservative (200) | PASS — owner must NOT attach billing to this key |
| GitHub | Free public/private repos | n/a | No | n/a | code + Second Brain git | free | ample | PASS |
| GCP e2-micro (fallback) | Always Free e2-micro | GCP REQUIRES a billing account on file | YES — egress/disk over free limits can bill | Documented as disaster-recovery ONLY; nothing auto-provisions it; owner action required | 0 (dormant) | Always Free limits | n/a | CONDITIONAL PASS — owner-initiated only; keep budget alerts + no auto-use |
| n8n (self-hosted) | OSS on own VM, SQLite | n/a | No | n/a | 1 daily cron + webhooks | n/a | n/a | PASS |
| Paid AI (any) | — | — | — | ALLOW_PAID_AI hard-false; router L3 raises PAID_DISABLED; agent_runs.cost_usd CHECK (= 0) | 0 | 0 | 0 | PASS (disabled) |

## Standing rules

1. `ALLOW_PAID_AI=false`, `ALLOW_PAID_INFRASTRUCTURE=false`, `MAX_*_PAID_AI_USD=0` —
   config.py ignores env overrides for the USD caps (pinned 0.0).
2. The database enforces `CHECK (cost_usd = 0)` on agent_runs: a nonzero cost cannot
   even be recorded.
3. On any free-allocation exhaustion: STOP task → PERSIST state (job → blocked) →
   LOG reason (events) → NOTIFY owner (notifications + webhook). Never fall through
   to a paid path.
4. No artificial utilization / anti-reclamation tricks. Reclamation is handled by
   recovery (infra/oracle/RUNBOOK.md), not prevention.
