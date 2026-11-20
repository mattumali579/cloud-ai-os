# Jarvis Glasses — Cursor Implementation Contract
Status: **LOCAL IMPLEMENTATION IN PROGRESS; DESKTOP PATH PROVEN; NOT HARDWARE-VERIFIED** as of 2026-11-12. See JARVIS_GLASSES_PROGRESS.md.
Canonical tracking issue: https://github.com/mattumali579/cloud-ai-os/issues/22

## User's actual goal
Ray-Ban Meta glasses should act as a full voice remote for the owner's **existing** Jarvis Windows computer, as far as the glasses/Android API legitimately allows. From speech, Jarvis should perform multiple real Windows/browser/app/file actions, track follow-up context, verify completion, and speak the result in the glasses. Do **not** build another one-turn chatbot or a static list of app-name command handlers.

This document is an execution contract for **Cursor Agent running in the real local Windows checkout**. Reading this document, creating an issue or writing code is *not* completion.

## Baseline facts and constraints
- Repo: `mattumali579/cloud-ai-os`, default branch `master`.
- Local checkout reported: `C:\Users\Admin\cloud-ai-os`; inspect actual workspace location, branch, untracked changes and any local-only files before changes. Never overwrite local changes or force-pull.
- Hardware: Ray-Ban Meta glasses, Samsung SM-S908U1, Android 16, Visor 3.0.4; Jarvis HP ProDesk 600 G6 Mini on Windows 11 Pro. Local laptop/phone remote access must keep working.
- Existing repo evidence: `src/cloudos/api/phone.py` provides single-task private Tailscale phone interface; `scripts/host_worker_supervisor.py`, `scripts/host_worker_boot.py`, `scripts/register-host-worker-task.ps1`, `docs/MINI_PC_OPERATIONS.md` implement a single host worker; `.cursor/mcp.json` registers Playwright; service orchestration is in `infra/docker/`. **Verify actual runtime health.**
- Previous discussions mention local-only `src/cloudos/glasses/jarvis.py`, `scripts/jarvis_glasses.py`, `Fix-Jarvis-Voice-Control.ps1`, `Fix-Jarvis-FollowUp.ps1`, and tasks called `CloudAIOS-HostWorker`. These paths were **not found on GitHub master**; first check local working tree and backups. Some services may be configured differently.
- Known failures: voice session cuts off after first command; "computer access not available"; instruction text dumped into Google search; Chrome opens but rest of task never executes; user says another request but glasses don't listen; false completion; one-off commands need manual re-wake; Visor/Meta Live AI startup failure. Instrument each separately.
- Never assume "Hey Meta" is supported by Visor, that firmware permits indefinitely open microphone sessions, or that MCP automatically gives the glasses unrestricted tool execution. Prove interface capabilities on the installed build.
- Source projects for evaluation, **not guaranteed solutions**: Visor https://github.com/macedonga/visor and `sbroenne/mcp-windows` https://github.com/sbroenne/mcp-windows. Favor existing Windows tool bridge if it already works.

## The intended chain and contract
```text
Authorized user speech
  -> glasses microphone/session
  -> Visor Android conversation + permission to issue tool commands
  -> authenticated private phone/Tailscale bridge
  -> existing Jarvis API/router and persistent conversation/task state
  -> existing Windows host worker / GUI automation tool
  -> real visible Windows/browser/file result (verified independently)
  -> status/result to Visor and speech back to glasses
  -> follow-up listening (only when supported) or explicit resume/wake
```
Every command must have correlation IDs to trace across layers: `event_id`, `conversation_id`, `request_id`, `job_id`, `tool_call_id`, result and speech delivery outcome. No credentials or private conversation details in public logs.

## Implementation order (diagnose before coding)

### 0. Workspace and capability audit
- Check `git status -sb`, current branch, `git remote -v`, local-only paths, virtual environment, versioned state, `AGENTS.md`, `.cursor/mcp.json`, app/phone integration, actual device logs and native Windows desktop session.
- Confirm Visor 3.0.4's exact supported app API/VDK/MCP paths and direction of tool calls. Test whether the glasses own the live audio session or Meta AI competes; identify microphone/background/listen limitations with evidence.
- Check private network state (Tailscale or existing TLS), worker/queue/REST auth, and whether UI automation executes on logged-in interactive desktop. Avoid exposing private endpoints publicly.
- Add a concise failure matrix to `docs/JARVIS_GLASSES_PROGRESS.md`. Do not diagnose from ChatGPT recollection alone.

### 1. Prove actual Windows control **before** Visor wiring
- Reuse working host-worker/Playwright components; integrate exactly one Windows UI automation provider as needed (UI Automation accessibility tree + screenshot/mouse/keyboard fallback).
- Validate harmless visible `open Notepad -> type unique marker -> inspect window title -> save in test directory -> read file back`. Verify from filesystem and screenshot/logs.
- Enable app launch/close, keyboard shortcuts, scrolling/clicking, windows, screen observation, browser navigation via Playwright where possible, file search, safe shell commands as agent tools rather than hardcoded every app.
- Ensure a clear permission/availability error when the desktop is locked, isolated in session 0, permissions missing or tool unavailable. Never fabricate that a UI action happened.

### 2. Route voice intents to tools, not to search
- Integrate authenticated requests from Visor to the existing `src/cloudos/api/phone.py`/Jarvis routing surface after inspecting actual code. Reuse request auth, no naked network scripts or separate public webhook.
- Distinguish "do something on my Windows computer" from informational search. Ask a targeted question only if genuinely ambiguous.
- Return structured `accepted/running/completed/failed/cancelled/confirmation_required` with real job IDs and meaningful outputs.
- Sanitize model output when reading remote web content; web page instructions cannot become host actions. Restrict outbound actions by policy.

### 3. Multi-turn voice + persistent agent executor
- Implement state machine `wake/listen -> transcription -> intent -> authorize -> plan/tools -> validate -> concise speech -> listen/follow-up/end`.
- Preserve session ID, context and tool results for "now click that" and "then save it"; bounded follow-up window and idle timeout. Use what Visor actually supports. If continuous mic capture is unavailable, give a working explicit resume phrase, **not** a false always-on claim.
- Persist long-running jobs through model turns and phone disconnects; poll/reconnect to status. Don't halt a multi-action task after its first tool or ask "what's next?" after a step that already has remaining work.
- Include cancellation/stop, idempotency to prevent duplicate irreversible actions after reconnect, retry with bounded backoff and recorded outcomes. One failed integration path twice -> reassess architecture, don't repeat the same patches.

### 4. Safe, private control
- Per-user/device authentication, transport through private Tailscale (or existing equivalent authenticated private link). No network-wide desktop privilege or embedded access tokens in repo; redact secrets in traces.
- GUI worker least privilege, owner session only; never silently disable Windows lock screen, Defender or UAC to "fix" automation.
- Require confirmation for delete/overwrite beyond reversible tests, external sends/submissions, payments, financial actions, password/credential access, security settings, installs and privilege escalation. Status and cancellation available at all times.
- Never expose raw webpage instructions as high-trust system prompts. Keep existing no-unapproved-paid-inference requirement.

### 5. Reliability
- Reuse single `CloudAIOS-HostWorker` supervisor and existing Docker Compose. Do not start duplicate worker/API, add gratuitous n8n workflows, another UI dashboard or custom cloud server.
- Demonstrate recovery from worker restart, phone app reconnect and Tailscale disruption. Check both laptop-to-Jarvis and phone-to-Jarvis functions remain operational.

## Mandatory evidence-based acceptance test
For each, record **observed vs simulated**, timestamps, traces, screenshot or actual outputs, and pass/fail:
1. On glasses: "Jarvis, open Chrome." Chrome actually opens on target Windows host; spoken result states **confirmed** only after Windows worker success.
2. Follow up "Search LSU construction management admissions" without the wake phrase during a verified supported follow-up period. Browser searches and the spoken answer corresponds to displayed page.
3. "Open Notepad, type GLASSES-JARVIS-TEST, save to Desktop as jarvis-glasses-test.txt." Verify actual file path and contents; spoken confirmation afterward.
4. Context-sensitive "click that" uses screen/UIA observations and requests clarification if multiple target elements.
5. Longer task continues to completion or can be cancelled with "Stop Jarvis," with no unbounded loop and no hidden actions.
6. Unauthenticated/unpaired requests are rejected; no secrets logged.
7. The reply-to-listen transition is proven with Visor 3.0.4, or actual app/firmware limitation demonstrated and documented honestly.
8. Reconnect after phone / private-network break; idempotent resume and truthful status, no duplicate destructive actions.
9. At least one **physical glasses -> Android -> Jarvis -> actual Windows GUI -> verified result -> spoken audio in glasses** test. A simulated API test does not count as hardware proof.

## Cursor operating instructions
- Work in one coherent implementation stream, starting with the first broken layer. Implement directly; don't stop at a plan or generic apology about no computer access.
- Before changing files, examine current Git status, local-only Jarvis code, tests, logs, configuration and app constraints. Preserve user changes. Prefer small patches and existing tools.
- Keep `docs/JARVIS_GLASSES_PROGRESS.md` continuously accurate with tested outcomes, evidence, explicit limitations, current blocker and resume instructions. Re-read it after any context reset.
- Commit working changes to a dedicated feature branch if possible; never overwrite uncommitted local work; test and report exact command outputs.
- The user authorizes **routine reversible tests** on their own devices, not destructive/security-sensitive operations. Stop for true hardware permission/auth/app-store actions when user input cannot be automated.
- Final outcome must say either **HARDWARE VERIFIED** with evidence or **BLOCKED** with exact failing hop and minimal user action; never claim full success just because software builds.
- Reference: GitHub issue #22 is the acceptance tracking record.
