# Jarvis Glasses — Implementation Progress / Cursor Resume Ledger

**Updated:** 2026-11-13 23:50:00Z (local 2026-11-13 17:50 America/Chicago)
**Current status:** `BLOCKED` (hardware Cap2 still needs spoken E2E; software expanded — close apps native)

**Issue #25 vision:** see [JARVIS_GLASSES_VISION_PROGRESS.md](JARVIS_GLASSES_VISION_PROGRESS.md). Software pipeline + 16 vision tests landed in worktree `cursor/issue-25-glasses-vision` and synced additively into this dirty checkout; **bridge not restarted**; camera hardware **not** verified. Preservation manifest: `_preservation/issue25_2026-11-13_17-33-46/`.

**Failing hop:** Cap2 spoken proof not yet observed this session. Software path ready: bridge Running, Visor connected; Chrome close is native (not Codex).

**Minimum user action:**
1. Keep Ray-Ban Meta glasses worn; Visor open on the Samsung phone.
2. Speak: “Hey Jarvis, open Chrome.” Then during listen: “Now search LSU construction management admissions.”
3. Confirm Chrome opens, LSU search runs, both spoken replies hear through glasses, same `conversation_id` in `logs/jarvis-glasses.log` and `ops_audit.activity_log`.

## Sources of truth
- Implementation contract: [JARVIS_GLASSES_IMPLEMENTATION.md](JARVIS_GLASSES_IMPLEMENTATION.md)
- Acceptance issue: https://github.com/mattumali579/cloud-ai-os/issues/22 (open; do not close until hardware E2E)
- Local code authoritative: `src/cloudos/glasses/`, `scripts/jarvis_glasses.py`, `scripts/run-jarvis-glasses.ps1`

## Patch review / repairs this session
Preserved prior correct patches (PS 5.1 `Continue`, sanitized audit, session routing, Bluetooth 30s backoff, correlation IDs, Credential Manager audit URL). Operator voice timeout raised 120s → **900s** (2026-11-13 ~01:51) so Codex is not killed mid-task.

Additional fixes 2026-11-13 ~00:15–00:56 local:
1. **Audit URL configured** in Windows Credential Manager (`CloudAIOS/JarvisGlasses/AuditDatabaseUrl`). Cleaned a markdown/placeholder paste; session-pooler URI now valid.
2. **Audit write verified** earlier (rows **22–24**). Restart ~00:56 hit Supabase `ECIRCUITBREAKER` (too many auth failures) — bridge still runs; audit cools separately.
3. **Phone integration Test C** flexible live report shape.
4. **YouTube play** opens `/watch` URL (not search-only).
5. **Desktop control expanded** (still not unrestricted): type, click UIA, press keys/chords, scroll, focus window, skip/next.
6. **Documents + confirm-gated send/delete** (this pass):
   - `list_recent_documents` / `read_document` / `edit_document` (append/replace with `.bak`) under Documents/Desktop/Downloads/OneDrive
   - Editor shortcuts: save / select all / copy / paste / undo / cut
   - Outbound **click send** / **send message** / named editable **delete** → `risk_confirm` then `confirm send` / `confirm delete` (60s TTL); cancel supported
   - Pay / password / security / wipe / “delete everything” stay hard-blocked
   - Help text updated; multi-step planner knows list/read/edit/confirm/copy/paste verbs
7. **Audit / manage / fix** (this pass):
   - `audit` / `diagnose` / `system check` → local `system_audit_report` (API, n8n, uptime, memory, disk, top processes)
   - `manage Docker|n8n|dashboard` → open that surface; bare `manage` → audit + hints
   - `fix` / `repair` / `troubleshoot` → host Codex operator job; high-risk verbs refused at voice edge
   - `audit my documents` still lists documents (not a system audit)
8. **Grok Bot open fix** (2026-11-13 ~01:10–01:25 local):
   - Root cause 1: Start-menu resolve missed, then fuzzy file search opened `oTQPxPFROck.md` for “rock bot”
   - Root cause 2 (still-error): Visor often transcripts only `hey jarvis open` (app name dropped) → “named open”
   - Fix: direct Grok Bot.exe; STT aliases; bare `open` asks which app; session follow-up `grokbot` → open; focus if already running
   - Live verify: focus/open confirmed; bridge restarted after follow-up patch
9. **Grok Bot AI control** (2026-11-13 ~01:35 local):
   - Voice: “tell Grok Bot about …”, “ai do …”, “read my new information”, “what did Grok say”
   - Focuses Prompt Edit, types payload, Enter, waits up to ~45s, speaks latest reply text
   - Note: Grok Bot UI currently shows usage ran out — prompts may not generate new replies until quota refills; read-last-result still works
10. **Operator timeout** (2026-11-13 ~01:51 local):
   - Symptom: spoken “could not finish … within two minutes” while Codex was still working
   - Cause: `GENERIC_TASK_TIMEOUT_S = 120` in `scripts/jarvis_glasses.py` for inline voice Codex
   - Fix: 900s operator budget; native 90s / grok_ai 120s wait windows; timeout speech no longer says two minutes
11. **Bare Jarvis wake word** (2026-11-13 ~01:53–02:02 local):
   - Visor `voice.requests` phrases now include bare + Hey forms
   - Private `voice.transcripts` path is **down**: speech recognition error 8 (recognizer busy) on open — no transcript events ever logged
   - Glasses Meta AI settings found: `custom_wake_word_enabled` + `assistant_wakeword`
   - **Committed on glasses (worn):** `assistant_wakeword=Jarvis`, `custom_wake_word_enabled=true` (Hey Meta still on)
   - This is Meta AI’s custom wake detector (not a Visor-only hardware hotword). Visor still claims questions that contain `Jarvis` / `Hey Jarvis`
   - Private transcripts remain flaky (error 8 busy); keep Visor on-screen
   - Test: **“Jarvis, open Chrome”** or **“Jarvis”** then the command; fallback **“Hey Jarvis …”**
12. **Close Chrome / apps** (2026-11-13 ~02:15 local):
   - Symptom: “close chrome” went to Codex operator and never quit the browser
   - Fix: native `close_named_target` — UIA window close, then `taskkill /F /T` for Chrome/Edge/Firefox/Brave/Docker/Cursor/Notepad/Grok Bot; also close tab (`Ctrl+W`) / active window (`Alt+F4`)
   - Classify `close`/`quit` before operator; planner splits multi-step with close/quit
   - Live: `close chrome` → Confirmed Chrome is closed; bridge task restarted with new code

**Rollback:** revert jarvis.py + tests + progress doc; remove Credential Manager target if needed. No git reset/pull.

## Tests
```text
$env:PYTHONPATH = "C:\Users\Admin\cloud-ai-os\src;C:\Users\Admin\cloud-ai-os"
.\.venv\Scripts\python.exe -m pytest tests/test_jarvis_glasses.py tests/test_jarvis_glasses_audit.py tests/test_jarvis_phone_integration.py tests/test_jarvis_intent_policy.py -q --tb=line
```
Result: **75 passed** on `tests/test_jarvis_glasses.py` (2026-11-13 08:20Z) after close-app tests.

## Runtime health
| Check | Evidence |
|---|---|
| `CloudAIOS-HostWorker` | Running (assumed; not re-broken) |
| `CloudAIOS-JarvisGlasses` | Running (restarted ~02:20 local after close-app patch) |
| Bridge uniqueness | One logical subscriber (venv stub + Python312 child) |
| Glasses | After restart: `worn=True`, `folded=False`, battery ~80–84% |
| Phone Tailscale | prior online |
| Visor LAN | Connected (`192.168.1.105:7333`) |
| Audit DB | Session pooler configured; restart logged `ECIRCUITBREAKER` — wait before re-verify writes |

## Audit / correlation
- `JARVIS_AUDIT_DATABASE_URL`: **configured** via Credential Manager; bridge loads via `ensure_audit_database_url_env()`.
- Manual verify rows 22–24 earlier; post-restart writes pending circuit-breaker cool-down.
- Folded-glasses mic pauses still rate-limited (60s fingerprint cooldown).

## Capability matrix
| Capability | Automated | Physical |
|---|---|---|
| 1. Multi-step single utterance | pass | pending |
| 2. Multi-turn session context | pass (software) | **pending spoken Cap2** |
| 3. Hands-free follow-up without re-wake | software armed | **pending** — wear + speak Cap2 |
| Documents list/read/edit | pass (unit) | pending |
| Confirm send / confirm delete | pass (unit) | pending |

## Voice examples (software-ready)
- “Hey Jarvis, check my documents.”
- “Hey Jarvis, read the file notes.txt.”
- “Hey Jarvis, append hello to notes.txt.”
- “Hey Jarvis, replace draft with final in notes.txt.”
- “Hey Jarvis, save.” / “copy” / “paste” / “undo”
- “Hey Jarvis, click send.” → then “confirm send”
- “Hey Jarvis, delete the file doomed.txt.” → then “confirm delete”
- “Hey Jarvis, audit the system.” / “manage Docker.” / “fix n8n.”
- Still blocked without confirm path: pay, passwords, security, wipe, delete everything

## Acceptance suite (issue #22)
| # | Criterion | Result |
|---|---|---|
| 1–3, 9 | Physical glasses E2E + spoken replies | **BLOCKED** until Cap2 spoken proof |
| 4–8 | click/cancel/auth/reconnect software | prior pass; not re-broken |
| Audit writes | earlier PASS rows 22–24; restart cooling from circuit-breaker |

## Do not claim
- Do not call native handlers “general unrestricted computer control.”
- Do not claim HARDWARE VERIFIED without worn-glasses Cap2 observation.
- Do not close issue #22 while blocked.

## Next agent / owner action
1. Wear glasses (currently look worn) → run Cap2 spoken script while bridge shows `Private hotword ready`.
2. Correlate both turns via same `conversation_id` in bridge log + `ops_audit.activity_log` (after audit circuit-breaker clears).
3. Optional physical smoke: check documents / click send + confirm send once a draft UI is focused.
