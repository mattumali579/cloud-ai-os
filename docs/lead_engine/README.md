# Fresh lead generator

Finds small and medium businesses you have never contacted, checks their own
website for a published email, grades them, removes anything already in your
history, saves them in the Cloud AI OS database, and hands the good ones to the
existing sender. It runs by itself in the cloud every 6 hours and only does real
work when the pile of ready leads is low.

- Code: `src/cloudos/leadgen/`, command: `lead_engine.py`
- Settings (targets, industries, cities): `config/lead_engine.yaml`
- Database tables: `db/migrations/006_lead_engine.sql` (Supabase, project `acq…`)
- Schedule: `.github/workflows/lead-engine.yml` (public repo = free runner minutes)
- Worker job type: `lead.engine.cycle` (same engine, for the local/Oracle worker)

## START

It is already scheduled. To run a refill right now:

```
gh workflow run lead-engine.yml -R mattumali579/cloud-ai-os -f mode=force
```

From this laptop instead:

```
python lead_engine.py cycle --force
```

## STOP

```
gh workflow disable lead-engine.yml -R mattumali579/cloud-ai-os
```

(`gh workflow enable …` turns it back on.) To pause only one source, set
`enabled: false` under `sources:` in `config/lead_engine.yaml`.

## STATUS

```
python lead_engine.py status
python lead_engine.py reports     # rewrites lead_engine_status.md + lead_source_performance.md
```

Each cloud run also prints both reports on its GitHub run page.

## TEST

```
python lead_engine.py test                 # ~10 real companies end to end, then re-submits them and requires 100% duplicates
python lead_engine.py discover --target 100
python -m pytest tests/test_leadgen_unit.py
```

## RECOVERY

- A source that fails 3 times in a row is benched automatically (15 min, doubling,
  max 24 h) while the other sources keep working. See the Health column in
  `lead_source_performance.md`. After fixing it: `python lead_engine.py reset-source google_maps`
- History must be loaded before discovery. Re-running is safe (idempotent):
  `python lead_engine.py import-history`
- Handoff trouble never undoes discovery; re-run `python lead_engine.py handoff`.
- Airtable free plan: 1,000 records per base. The handoff keeps at most
  `airtable_ready_window` engine leads waiting there and never pushes past
  `airtable_record_ceiling`.

## Rules the engine enforces

- A lead is a company. Duplicate keys: website domain, then name + city + state,
  then old ledger record ids, then email/phone as backup evidence. Email
  providers (gmail.com, yahoo.com, …) are never used as a company identity.
- Emails are only ever taken from the business's own public pages or its public
  map listing. Nothing is guessed. `validated` means the mail server exists; the
  mailbox itself is not probed.
- A company counts as contacted only when the sender stamps "Emailed At".
  Drafted, queued and skipped are not sent.
- No Apify, no paid APIs, no CAPTCHA bypassing; robots.txt is honoured.
