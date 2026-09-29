# Lead engine status

Generated 2026-09-29 06:11 UTC from the live database.

| Measure | Count |
|---|---|
| Historical companies (imported from old ledgers) | 336 |
| Newly discovered companies | 356 |
| Total unique companies | 692 |
| Domains indexed for dedupe | 670 |
| Never-contacted companies | 504 |
| Qualified (HIGH+MEDIUM), all | 124 |
| Qualified NEW companies (counts toward goal) | 93 |
| Outreach-ready, waiting in queue | 48 |
| Handed to sender, not yet sent | 61 |
| Contacted (provider-confirmed) | 188 |
| Duplicates rejected at discovery | 152 |
| Rejected (chain, dead, directory...) | 316 |
| Invalid contacts | 2 |
| Progress toward 4,000 unique qualified | 93 / 4,000 (2.3%) |

Refill rule: when ready+handed-off inventory (109) drops below 300, discover until it is back to 500.

## Recent runs

| Mode | Started (UTC) | Minutes | New companies | New outreach-ready | Duplicates | Source errors |
|---|---|---|---|---|---|---|
| cycle | 09-29 06:05 | running | - | - | - | 0 |
| cycle | 09-29 05:56 | 3 | failed: QueryCanceled: statement timeout (collided with a manual imp | - | - | - |
| discover | 09-29 05:48 | 7 | 57 | 22 | 63 | 0 |
| discover | 09-29 05:42 | 5 | 113 | 31 | 18 | 1 |
