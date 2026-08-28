# SECURITY — Privacy Labels, External-AI Gate, Secret Scanning

Owner: Agent 10. Implements ARCHITECTURE.md §10 (privacy contract) and §9-L2
(external-AI routing). Everything documented here is **deterministic** — rules
and regexes only, never AI-decided. The fail-safe direction is always *more
private*: an unknown label, an invalid frontmatter value, or an unreadable
config classifies as SENSITIVE and the gate blocks.

## 1. Privacy labels and default routing

| Label | Meaning | Local AI (L0/L1 on our infra) | Free external AI (Gemini, §9-L2) |
|---|---|---|---|
| `public` | Intended for the open web | yes | **allowed** (after secret scan) |
| `internal` | Working notes, no harm if leaked but not published | yes | **only** if the note's frontmatter sets `external_ok: true` (then secret scan) |
| `personal` | About the owner's life, identifiable | yes | **never** |
| `sensitive` | Finance, health, credentials-adjacent, other people's data | yes | **never** |
| `secret` | Credentials and key material | yes* | **never** |

\* "yes" for local levels means the privacy gate does not stop L0/L1; the
router still applies its own budget/allowlist rules. Only the L2 external hop
is gated here.

How a file gets its label — `classify(path, frontmatter, default=None)`,
strict precedence:

1. **Explicit frontmatter** — `privacy:` (the key name comes from
   `frontmatter_key` in `config/privacy.yaml`). The value is matched
   case-insensitively (`Public` == `public`). A present-but-invalid value
   (e.g. `privacy: wide-open`) fails safe to `sensitive`.
2. **Path rules** from `config/privacy.yaml` — globs matched against the
   posix-normalized relative path (`\` → `/`, leading `./` stripped,
   case-insensitive because the Second Brain lives on Windows/macOS
   filesystems). `external_ai.blocked` globs ⇒ `sensitive`;
   `external_ai.allowed` globs ⇒ `public`. **Blocked wins** when both match.
   `**` crosses directory separators; `*` and `?` never do.
3. **Default** — the `default` argument if the caller passed one, else
   `default_label` from `privacy.yaml` (`internal`).

If `privacy.yaml` is missing or unreadable, the loader returns a fail-closed
config: default label `sensitive`, no allowed globs — every external request
is blocked until the config is restored.

## 2. Gate flow

`gate(content, label, frontmatter=None) -> GateDecision(allowed, reason, hits)`

```
content + label (+ frontmatter)
        │
        ▼
┌───────────────────────────────┐
│ 1. LABEL CHECK                │
│  public          → continue   │
│  internal        → continue   │──── external_ok truthy? no → BLOCK
│  personal        → BLOCK      │     ("internal_requires_external_ok")
│  sensitive       → BLOCK      │
│  secret          → BLOCK      │     ("label_blocked:<label>")
│  unknown/invalid → BLOCK      │     ("unknown_label")
└───────────────┬───────────────┘
                ▼
┌───────────────────────────────┐
│ 2. SECRET SCAN (full content) │
│  any hit → BLOCK              │     ("secret_detected", hits = safe
│  no hits → ALLOW ("allowed")  │      metadata only)
└───────────────────────────────┘
```

Both checks must pass. The scan always runs on the **full outbound payload**,
even for `public` content — a public architecture doc with a pasted key is
still blocked. `external_ok` is parsed strictly: only `true`/`yes`/`1`/`on`
(bool `True`, nonzero number) count; the string `"false"` is **not** consent.

## 3. Secret patterns

All patterns are named, ordered, and pure-regex. `env_assignment` additionally
requires the value to be ≥12 non-space chars with Shannon entropy > 3.5
bits/char, which is what keeps ordinary prose and toy values out.

Examples below are **obviously fake** — never put a real secret in docs or tests.

| Pattern | Shape | Catches (fake examples) |
|---|---|---|
| `aws_access_key` | `AKIA` + 16 upper/digit | `AKIAIOSFODNN7EXAMPLE` |
| `google_api_key` | `AIza` + 35 base64ish | `AIzaFake1Fake1Fake1Fake1Fake1Fake1Fake1` |
| `slack_token` | `xox[baprs]-` + body | `xoxb-1234567890-NOTREALNOTREAL` |
| `github_token` | `gh[pousr]_` + 36+ alnum | `ghp_Ab1Cd2Ef3Gh4Ab1Cd2Ef3Gh4Ab1Cd2Ef3Gh4` |
| `stripe_key` | `sk_live_` / `rk_live_` + 16+ | `sk_live_FAKEfake1234FAKEfake` |
| `private_key_block` | PEM header | `-----BEGIN RSA PRIVATE KEY-----` |
| `jwt` | `eyJ…`.`eyJ…`.`sig` | `eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.FAKEsig12345678` |
| `connection_string` | scheme`://user:pass@`host (postgres/postgresql/mysql/mongodb(+srv)/amqp/redis) | `postgresql://fakeuser:fakepass123@db.example.com/app` |
| `authorization_header` | `Authorization: Bearer\|Basic` + 16+ chars | `Authorization: Bearer FAKEFAKEFAKEFAKE1234` |
| `env_assignment` | `(api[_-]?key\|secret\|token\|passw(or)?d)` in an identifier, then `=`/`:` and a long high-entropy value | `API_KEY="Zq83vN1pXk7Rt2Lm9Jd4"` |

Deliberate negatives (no hit): `PASSWORD=test` (short),
`SECRET=aaaaaaaaaaaaaaaaaaaa` (low entropy), "the token bucket algorithm"
(no assignment), `postgresql://localhost:5432/app` (no credentials).

**Redaction invariant:** a `SecretHit` carries only `pattern_name`,
`span=(start, end)`, and `redacted_sample` — at most the first 4 characters
plus `…`. The full secret never appears in any return value, log, event,
error, or test assertion.

## 4. Extending config/privacy.yaml

The four top-level keys are FIXED by contract (`default_label`,
`frontmatter_key`, `internal_external_ok_key`, `external_ai`). You may
**append** globs to `external_ai.allowed` / `external_ai.blocked`; never
rename keys. Guidance:

- Prefer adding to `blocked` — a wrong `blocked` entry costs convenience, a
  wrong `allowed` entry costs privacy.
- Globs are matched case-insensitively against posix-style relative paths.
  Use `dir/**` for a subtree, `**/name*` for a filename anywhere.
- After editing, call `cloudos.privacy.reset_privacy_config_cache()` (and
  `cloudos.config.reset_settings_cache()` if the path changed) or restart the
  process — the config is cached.

## 5. Incident behavior (secret detected / privacy blocked)

**BLOCK REQUEST → LOG SAFE METADATA → NOTIFY OWNER. Never log the secret.**

1. **Block** — `gate()` returns `allowed=False`; the router raises
   `CloudOSError(SECRET_DETECTED)` or `CloudOSError(PRIVACY_BLOCKED)`; the
   worker marks the job `blocked` (no retry, per §8).
2. **Log safe metadata only** — pattern name, span offsets, redacted sample
   (≤ 4 chars + `…`), source path, content hash. Nothing else.
3. **Notify owner** — via `cloudos.notify` / the notifications table so a
   human rotates the credential and cleans the source note.

Failure-mode rows (for docs/FAILURE_MODES.md, Agent 10 section):

| Failure | Detection | Automatic response | Owner action |
|---|---|---|---|
| Secret accidentally included in an outbound prompt | `scan_secrets` on the full payload before any external send | Request blocked (`secret_detected`), job → `blocked`, event + notification with pattern name/span/redacted sample only | Rotate the credential; remove it from the source note; re-run |
| PERSONAL note requested for external AI | Label check in `gate()` (`label_blocked:personal`) | Request blocked, `PRIVACY_BLOCKED`, job → `blocked`, owner notified; content never leaves the machine | Re-classify deliberately (frontmatter) only if genuinely shareable, or run it on local levels |

## 6. Why free Gemini traffic is gated so hard

The L2 level uses Google's **unpaid** Gemini API tier. Unpaid API traffic may
be used by the provider for service improvement — i.e. anything we send can be
read, retained, and trained on. That is an acceptable trade for `public`
content and for `internal` notes an owner has explicitly marked
`external_ok`, and an unacceptable one for everything else. Hence the hard
rule: **PERSONAL, SENSITIVE, and SECRET content is never sent to external
AI**, no override key exists for those labels, and even allowed content is
secret-scanned in full first.
