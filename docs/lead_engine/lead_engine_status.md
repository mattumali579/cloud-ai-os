# Lead engine status

Generated 2026-10-05 07:55 UTC from the live database.

| Measure | Count |
|---|---|
| Historical companies (imported from old ledgers) | 0 |
| Newly discovered companies | 719 |
| Total unique companies | 719 |
| Domains indexed for dedupe | 698 |
| Never-contacted companies | 719 |
| Qualified (HIGH+MEDIUM), all | 205 |
| Qualified NEW companies (counts toward goal) | 205 |
| Outreach-ready, waiting in queue | 205 |
| Handed to sender, not yet sent | 0 |
| Contacted (provider-confirmed) | 0 |
| Duplicates rejected at discovery | 106 |
| Rejected (chain, dead, directory...) | 258 |
| Invalid contacts | 30 |
| Progress toward 4,000 unique qualified | 205 / 4,000 (5.1%) |

Refill rule: when ready+handed-off inventory (205) drops below 300, discover until it is back to 500.

## Recent runs

| Mode | Started (UTC) | Minutes | New companies | New outreach-ready | Duplicates | Source errors |
|---|---|---|---|---|---|---|
| discover | 10-05 07:45 | 3 | 95 | 24 | 18 | 0 |
| cycle | 10-05 07:38 | 7 | failed: operator timeout after no observed commit; inventory verifie | - | - | - |
| discover | 10-05 07:25 | 10 | failed: operator timeout after partial commit; inventory verified se | - | - | - |
| discover | 10-05 06:11 | 11 | 180 | 58 | 28 | 0 |
| discover | 10-05 06:06 | 10 | 18 | 7 | 6 | 1 |
