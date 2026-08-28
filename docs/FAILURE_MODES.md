# Failure-mode matrix (addendum §26)

Expected behavior for each simulated failure. Rows are verified by the tests named
in the last column (or marked MANUAL for phase-gated scenarios).

| # | Failure | Expected behavior | Verified by |
|---|---|---|---|
| 1 | Gemini quota exhausted | Router fails closed: CloudOSError(QUOTA_EXHAUSTED); no request sent; worker sets job → blocked; event + owner notification | tests/test_router (budget tests) |
| 2 | Cloudflare Workers AI quota exhausted | Same fail-closed path at L1; no silent fall-through past budget | tests/test_router |
| 3 | Supabase unavailable | db.healthcheck() false; /healthz reports db:false; API job routes 503 DEPENDENCY_UNAVAILABLE; worker idles and retries connection; no data loss (state in DB, not memory) | tests/test_api, MANUAL (P2) |
| 4 | Supabase read-only | Writes raise → jobs fail with INTERNAL/DEPENDENCY error and retry with backoff; notification on repeated failure | MANUAL (P2) |
| 5 | Oracle service restarted | compose restart: always; migrate() idempotent at startup; running jobs' locks recovered (locked jobs with dead locked_by re-claimable after visibility timeout — see worker docs) | MANUAL (P5) |
| 6 | Oracle VM recreated | RUNBOOK: bootstrap.sh on fresh VM → operational; canonical data survives (GitHub/Git/Supabase) | MANUAL (P7 gate) |
| 7 | DB connection dropped mid-job | Worker catches, job attempts+1, retried with backoff to max_attempts | tests/test_worker |
| 8 | Worker crashes during job | Job left `running` with stale lock; on restart `queue.recover_stale()` requeues that instance's running jobs and writes a STALE_JOBS_REQUEUED event | tests/test_worker_queue |
| 9 | n8n unavailable | Core system unaffected (n8n is convenience); notifications fall back to notifications table (delivered=false) + notify.flush retries | design (notify.py) |
| 10 | Malformed webhook | API validates → 400 VALIDATION_ERROR (bearer/HMAC failure → 401/403); nothing enqueued | tests/test_api |
| 11 | Secret accidentally included in prompt | privacy.gate scan → SECRET_DETECTED; request BLOCKED before any network call; safe metadata logged (pattern name only); owner notified; job → blocked | tests/test_privacy, tests/test_router |
| 12 | Paid model requested | Router raises PAID_DISABLED (L3 unreachable; non-allowlisted hint → MODEL_NOT_ALLOWED); job → blocked; notify | tests/test_router |
| 13 | PERSONAL/SENSITIVE content routed to external AI | privacy.gate → PRIVACY_BLOCKED before send | tests/test_privacy |
| 14 | Free AI entirely unconfigured | Router → DEPENDENCY_UNAVAILABLE ("no free AI provider configured"); deterministic L0 tasks still work | tests/test_router |

Agent-reported additions are appended below as they are verified.
