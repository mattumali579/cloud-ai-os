# Lead history import report

Run 2026-09-29 with `python lead_engine.py import-history`. Re-running it is safe:
the second and third runs reported every record as `already_imported` and created
0 new companies.

## Sources imported

| Source | Raw records | New companies | Merged into an existing company | Contacts added |
|---|---|---|---|---|
| Airtable "B2B LeadGen" / Leads (`appQNVO…`) | 348 | 334 | 14 | 183 |
| Old CSV `leads_enriched_roofers_in_baton_rouge__la_20260820_163232.csv` | 20 | 2 | 18 | 0 |
| Cloud AI OS `email_outbox/sent` (2 send waves) | 11 recipients | 0 | 11 | 0 |
| Gmail Sent Mail since 2026-01-01 | 1,308 messages scanned; 675 outreach recipients | 0 | 675 | 11 |
| Airtable "B2B LeadGen" (`appnyLk…`) | 0 (base is empty) | 0 | 0 | 0 |

Gmail recipients only counted as outreach when the message carried the
pipeline's `X-Leadgen-Machine` header, or the recipient's email or company
domain was already in the ledger. 447 other recipients (personal mail, school,
job applications) were skipped.

## Result

| Measure | Count |
|---|---|
| Unique historical companies | **336** |
| Domains indexed for dedupe | 322 (14 companies had only a freemail inbox or no site) |
| Already contacted (never offered again) | 188 = 175 per Airtable + 13 only provable from Gmail Sent |
| Unsubscribed (never contact) | 2 |
| Never contacted (no email found at the time) | 146, re-checked by the history-recovery source |
| Duplicate records merged | 14 inside Airtable, 18 CSV rows, 11 outbox recipients, 675 Gmail recipients |

Cross-check: counting unique company keys straight from the raw Airtable
download (independent of the importer) gives 334 companies and 175 contacted.
Those match the importer's Airtable rows exactly.

## Bugs caught during import

- Some old ledger rows listed an email provider (yahoo.com) as the company's
  domain, which would have merged every Yahoo-email business into one company.
  Email providers are now never used as a company identity.
- Unsubscribed rows that also had a send date were stored as merely "contacted".
  Unsubscribe now takes priority.
- The engine's own handoff rows in Airtable are now skipped by the importer.
  They are synced back through the handoff step instead.

## Not importable

UNVERIFIED: Instantly campaign history. There is no Instantly API key on this
machine or in GitHub, and the Instantly lane never got past its schedule check,
so it is unlikely that any Instantly-only contacts exist outside Airtable.
AgentMail: its 300-lead discovery job never finished, so it never produced a
send list.
