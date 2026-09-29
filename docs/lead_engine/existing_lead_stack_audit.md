# Existing lead stack audit

Audited 2026-09-29 by inspecting the live repos, GitHub Actions run logs, the
Airtable bases, the Supabase database and the Gmail Sent folder. Every claim
below was checked against the running system, not a README.

## What exists

| Component | Where | State found |
|---|---|---|
| B2B leadgen pipeline (Node) | `AI-Second-Brain/leadgen-pipeline/cloud/*.js` (private repo) | Kept. Owns sending. |
| Google Maps discovery | `gosom/google-maps-scraper` Docker image, started inside GitHub runners | **Works** (20 listings/query locally and via the engine). Reused as a source. |
| AgentMail 300 discovery | `.github/workflows/agentmail-discover-300.yml` | **Broken**: one giant 120-query scraper job, 1-hour poll limit, all results discarded on timeout (last run 2026-09-26: "scraper did not complete: working"). Replaced by small per-query jobs in the lead engine. |
| Instantly 100 sender | `.github/workflows/leadgen-instantly-proof.yml` + `instantly_proof.js` | **Was broken**: every scheduled run resolved `phase=skip` (gate demanded minute == 30; GitHub starts runs late). Fixed by a separate agent (commits `2ce1024`, `4f25d23`, 41/41 gate tests). Still blocked: `INSTANTLY_API_KEY` secret is not set and 20 warmed sender inboxes are required. |
| Gmail SMTP sender | `leadgen-cloud-outbound.yml` | Deliberately disabled ("personal-Gmail first-touch sender is retired"). Left as is. |
| Suppression / ledger | Airtable base "B2B LeadGen" (`appQNVO…`), table Leads | 348 records. Airtable free plan caps a base at 1,000 records and ~1,000 API calls/month, so it cannot hold a 4,000-company pool. Kept as the sender's small intake tray. |
| Second Airtable base "B2B LeadGen" (`appnyLk…`) | Airtable | Empty (0 records). Ignored. |
| Cloud AI OS database | Supabase (project `acq…`), `DATABASE_URL` | Healthy, had **no lead tables**. Now holds the canonical company store. |
| Cloud AI OS worker / queue | `src/cloudos/worker` | Works; runs locally (not 24/7). New job type `lead.engine.cycle` added. |
| BrightReach service | `src/cloudos/brightreach` | Client-side lead handling (a customer's inbound leads), not prospecting. Not touched. |
| n8n | local Docker (`leadgen-n8n`, `cloudos` n8n) | Not running; old lead workflows are marked legacy in the pipeline README. Not used. |
| Old lead files | roofers CSV (20 rows), `email_outbox/sent` (2 waves) | Imported into history. |
| Gmail Sent | matt.umali579 Sent Mail, 1,308 messages since 2026-01-01 | Scanned; 675 outreach recipients matched to known companies, 447 non-outreach recipients ignored. |

## Dedupe before vs. after

- **Before:** per-script, email + exact domain against a fresh Airtable download each run. Nothing caught the same company under a different domain, a freemail inbox, or the same business listed twice.
- **After:** one database, company-level keys in order: normalized domain → normalized name + city + state → old ledger IDs → email/phone as secondary evidence. Email providers (gmail.com, yahoo.com…) are never treated as a company identity. That bug was found and fixed during import: the old ledger had "yahoo.com" as a company domain.

## Discovery sources, before vs. after

| Before | After |
|---|---|
| Google Maps scraper only, one monolithic job | OpenStreetMap (free, ~130-160 businesses with websites per metro in one query), Google Maps scraper in small jobs, recovery of old uncontacted companies |
| Louisiana + 5 Gulf cities, 10 trades | 107 US metros (Louisiana first), 22 industries |
| No memory of searched combinations | `lead_queries` table: each source/query is run once, and repeated after 30 days only if it previously produced new companies |

## Handoff to sending

`lead engine → companies.outreach_status = outreach_ready → Airtable Leads (Status "Ready", Signal carries the company id + qualification + facts) → Instantly lane picks up eligible rows`. A company becomes "contacted" only when the sender stamps **Emailed At**. Drafted, queued and skipped never count as sent.

## Kept / replaced

- Kept: Instantly sender, Airtable as intake tray, gosom scraper image, Gmail-sent evidence.
- Replaced: the monolithic AgentMail/Instantly discovery step (as a source of new leads) with the lead engine's small-batch, multi-source, self-healing discovery.
- Not rebuilt: sending, copywriting, reply handling.
