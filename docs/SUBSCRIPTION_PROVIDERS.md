# Subscription CLI provider layer — change spec & operations

Owner directive (2026-08-28): the AI execution layer must use existing fixed-price
subscriptions via officially supported subscription-authenticated CLIs. The goal is
NOT "free API usage" — it is **no AI cost above existing monthly subscriptions**.

## Cost invariant (redefined — supersedes the old reading in ARCHITECTURE.md §0)

```
FIXED SUBSCRIPTION COST   = owner-controlled (outside runtime cost calculation)
INCREMENTAL API COST      = prohibited
SURPRISE AI SPEND         = structurally impossible
```

`agent_runs.cost_usd CHECK (= 0)` now asserts **incremental metered AI cost = 0**.
Any code path capable of generating an incremental AI charge fails architecture
validation.

## Verified provider facts (researched 2026-08-28 — the reason this design exists)

| Provider | Fact | Consequence |
|---|---|---|
| Claude Code | Auth precedence: cloud-provider vars > `ANTHROPIC_AUTH_TOKEN` > `ANTHROPIC_API_KEY` > `apiKeyHelper` (settings.json) > `CLAUDE_CODE_OAUTH_TOKEN` > Anthropic profiles > subscription `/login`. **In `-p` (headless) mode the API key is ALWAYS used when present.** | Env scrubbing is mandatory; `apiKeyHelper`/profile presence = BILLING_RISK. `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` is the sanctioned subscription headless mode (Pro/Max/Team/Enterprise). |
| Codex CLI | Two auth modes: ChatGPT sign-in (plan-included usage) vs API key ("standard API rates"). Known issue: silently prefers `OPENAI_API_KEY` over ChatGPT login — real surprise-charge reports. `codex login status` reports the active mode; creds in `~/.codex/auth.json` (or OS keyring). Headless: `codex login --device-auth` (beta) or copied auth.json. | Scrub `OPENAI_API_KEY`; if auth.json carries API-key auth → BILLING_RISK; ChatGPT tokens → AVAILABLE_SUBSCRIPTION. |
| Gemini CLI | Auth methods: Login-with-Google OAuth (for AI Pro/Ultra subscribers), `GEMINI_API_KEY` (metered), Vertex (`GOOGLE_API_KEY` / `GOOGLE_APPLICATION_CREDENTIALS` / ADC — metered). Consumer OAuth login reportedly ended 2026-06-18 (gemini-cli #28439: CLI now demands `GEMINI_API_KEY` / `GOOGLE_GENAI_USE_VERTEXAI` / `GOOGLE_GENAI_USE_GCA`); state is in flux. | Google provider runs ONLY on cached OAuth creds (`~/.gemini/oauth_creds.json`) with every metered env var scrubbed. If OAuth is unusable → `UNAVAILABLE` (per owner rule: no silent fallback to API keys or Vertex, ever). |

## What changed (file by file)

1. `src/cloudos/contracts.py` — added `ErrorCode.AUTH_REQUIRED`, `ErrorCode.BILLING_RISK`,
   `ProviderState` enum (`AVAILABLE_SUBSCRIPTION | QUOTA_EXHAUSTED | AUTH_REQUIRED |
   UNAVAILABLE | BILLING_RISK`), `DEFER_CODES = {QUOTA_EXHAUSTED, AUTH_REQUIRED}`.
   `FAIL_CLOSED_CODES` now = {LIMIT_REACHED, PAID_DISABLED, PRIVACY_BLOCKED,
   SECRET_DETECTED, BILLING_RISK} (block, no retry). QUOTA_EXHAUSTED moved from
   block-forever to **defer**: preserve the task, retry after quota reset.
2. `src/cloudos/config.py` — REMOVED `cf_account_id`, `cf_workers_ai_token`,
   `workers_ai_daily_budget`, `gemini_api_key`, `gemini_daily_request_budget`,
   `free_model_allowlist_path`. ADDED `subscription_providers` (priority order,
   default `claude,codex,gemini`), `ai_retry_after_quota_minutes` (60),
   `ai_retry_after_auth_minutes` (240), `cli_timeout_seconds` (300),
   `claude_cli_bin/codex_cli_bin/gemini_cli_bin`, optional `claude_cli_model`.
3. `src/cloudos/router/providers/` — `workers_ai.py` and `gemini.py` (metered REST
   clients) DELETED. New: `base.py` (scrubbed env, `ProviderStatus`, subprocess
   runner), `claude_cli.py`, `codex_cli.py`, `google_cli.py`. No httpx anywhere in
   the router — there is no HTTP client left that could call a metered AI API.
4. `src/cloudos/router/levels.py` — routing is now: L0 deterministic →
   subscription CLIs in configured priority order → defer. `model_hint` must name
   an enabled provider (`claude|codex|gemini`) or MODEL_NOT_ALLOWED. Paid guard
   unchanged (PAID_DISABLED). Privacy gate + secret scan run before EVERY CLI
   invocation (the prompt leaves the process boundary either way).
5. `db/migrations/002_provider_status.sql` — observability table: provider,
   auth_mode, provider_type, state, last_success, last_auth_validation,
   task_count, last_failure_reason, cooldown_until. Router upserts after every
   probe/call. **Never stores tokens or credentials.**
6. `src/cloudos/worker/queue.py` — new `defer(conn, job, minutes, error_code, note)`:
   back to `queued` with future `run_at`, attempts NOT incremented (deferral is not
   failure). `loop.py`: `DEFER_CODES` → defer (+notify on AUTH_REQUIRED);
   `FAIL_CLOSED_CODES` → block as before.
7. `src/cloudos/api/store.py` — `/v1/quota` keeps its `{usage, budgets, fail_closed}`
   shape (n8n daily_ops compatibility) and adds `providers`; new `GET /v1/providers`.
8. `config/free_model_allowlist.json` — deleted (nothing metered left to allow).
9. `.env.example` — AI API key vars REMOVED entirely; new subscription vars added
   with the scrub warning.

## Environment scrubbing (enforced in `providers/base.py`)

Every provider subprocess runs with these variables REMOVED from its environment,
regardless of what the parent process has:

```
ANTHROPIC_API_KEY   ANTHROPIC_AUTH_TOKEN   ANTHROPIC_PROFILE
ANTHROPIC_FEDERATION_RULE_ID   ANTHROPIC_ORGANIZATION_ID
CLAUDE_CODE_USE_BEDROCK   CLAUDE_CODE_USE_VERTEX   CLAUDE_CODE_USE_FOUNDRY
OPENAI_API_KEY
GEMINI_API_KEY   GOOGLE_API_KEY   GOOGLE_APPLICATION_CREDENTIALS
GOOGLE_GENAI_USE_VERTEXAI
```

`CLAUDE_CODE_OAUTH_TOKEN` is deliberately NOT scrubbed: it is the subscription-backed
headless credential (`claude setup-token`), the sanctioned mode for VMs/CI.

## Provider status semantics

```
AVAILABLE_SUBSCRIPTION  binary present + subscription auth verified → may execute
QUOTA_EXHAUSTED         plan allowance exhausted → task deferred until reset window
AUTH_REQUIRED           no usable subscription credential → deferred + owner notified
UNAVAILABLE             binary missing / unsupported auth state → skipped
BILLING_RISK            a metered credential could be selected and cannot be ruled
                        out (apiKeyHelper configured, Anthropic profile active,
                        auth.json in API-key mode) → FAIL CLOSED, block + notify
```

BILLING_RISK is never retried automatically: it means "money could move" and only
the owner may clear it.

## Routing

```
task
 → deterministic/local (L0) if a registered implementation exists
 → claude_cli   (Claude subscription)      levels: 1
 → codex_cli    (ChatGPT subscription)     2
 → google_cli   (Google OAuth, only while officially usable)  3
 → NO PAID FALLBACK — CloudOSError:
      any provider QUOTA_EXHAUSTED-or-cooldown → QUOTA_EXHAUSTED (worker DEFERS job)
      all AUTH_REQUIRED                        → AUTH_REQUIRED  (defer + notify)
      any BILLING_RISK                         → BILLING_RISK   (block + notify)
      none installed/enabled                   → DEPENDENCY_UNAVAILABLE (retry/backoff)
```

## Deployment note (containers)

The subscription CLIs and their login state live on the HOST (or on the Oracle VM
after the owner logs in there once: `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`,
`codex login --device-auth`). The Docker worker image does not bundle the CLIs, so
containerized workers report providers UNAVAILABLE and AI jobs defer until a
CLI-equipped worker claims them. Run `python -m cloudos.worker` on the host for AI
jobs; non-AI jobs run anywhere.
