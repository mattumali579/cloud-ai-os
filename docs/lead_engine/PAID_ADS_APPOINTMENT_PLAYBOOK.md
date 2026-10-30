# BrightReach: Paid Ads → Qualified Leads → Booked Appointments
Owner-approved operating brief — 2026-10-30

## Mission
ONE OFFER: Generate qualified leads with client-funded Meta advertising, follow up promptly using existing Jarvis/n8n infrastructure, book verified appointments on the business's calendar, and report outcomes. Sell appointments, NOT an "AI OS."

Source video (faithfully follow its *sequence*, not its earnings claims): https://www.instagram.com/p/DezmSiKxu3Z/ (creator @noevarner.ai; 1m48s).
The spoken-video transcript was generated using yt-dlp + FFmpeg + Whisper on 2026-10-30. Preserve the original audio/transcript separately in a private authorized workspace; don't fabricate or reproduce an unverified script.

## Strategy translated from the video, in chronological order
1. Sell a service local businesses already need; use AI to deliver it.
2. Solve both consistent lead generation and booked appointments, not just chatbot installation.
3. Package creation/launch of Meta ads + automated follow-up + calendar booking + clear reporting as one offer.
4. Pick 2–3 niches with genuine customer economics and budget. Priority: owner-operated HVAC, roofing, plumbing/electrical, and gyms or fitness studios; test economics before committing. Avoid low-budget gyms and big chains with centralized marketing.
5. Check actual Meta Ad Library **in a logged-in browser**: United States → All ads → Active → advertiser name. For each company record checked_on, advertiser Page URL, ad identifiers/links/screenshots, start date, CTA, landing page and booking workflow, and explicit status ACTIVE / NOT FOUND IN SEARCH / UNVERIFIED. NEVER infer exact spend, ROAS, sales, or absolute non-advertising from the library.
6. Find comparable local firms with no active ad found *in that search*, qualify and audit them, then prepare specific outreach comparing a verifiable competitor creative with a possible booking improvement. Never assume all companies lack leads.
7. Prospect by personalized outreach and optional in-person visits. Prepare drafts first; follow existing OUTREACH_EXECUTION_RULES.md and verify sender permissions before any external send. Do not use unlawful mass spam.
8. Pitch a realistic first-client pilot: client funds its own ad spend; a proposed $1,000–$2,000 monthly service retainer is only a pricing hypothesis, not proof of demand. Once revenue is provider-confirmed, reinvest in better creatives and acquisition.

## Existing system (use rather than rebuild)
- Repo root: C:\Users\Admin\cloud-ai-os (Windows); read AGENTS.md, docs/OUTREACH_EXECUTION_RULES.md, docs/CREATIVE_QUALITY_STANDARD.md before changes.
- Current lead system: config/lead_engine.yaml, src/cloudos/leadgen/, n8n/brightreach_lead_engine.json, n8n/revenue_os_main.json, n8n/sales_optimizer.json, outreach sender & reply modules.
- As of repo inspection, config/lead_engine.yaml still targets **Google Reviews + Maps $299/mo**. That is the old offer; do not run conflicting campaigns. Migrate existing campaign carefully, protect historical data and test every change.
- Prospects sourced 2026-10-30: 38 Google Maps listings, 19 prioritized in https://docs.google.com/spreadsheets/d/1xCybJDrsykvu0QvNSuskz-VSZpgIItMm8D3CBqGvVuw/edit ; uncontacted and NOT confirmed buyers. Deduplicate by domain + Google Place ID against database before counting unique leads.
- Existing reported dual-worker file handoff proved worker mechanics, not distinct AI model collaboration. Do not require Codex credits.

## Milestones — Cursor must check boxes ONLY after real proof
- [ ] M1: On **Jarvis itself**, inspect live Docker + n8n workflow IDs, active status and latest failed executions; snapshot existing definitions; record baseline metrics and errors. Show evidence.
- [ ] M2: In Cursor Browser, open the official Meta Ad Library and audit **10 local competitors** (5 blue-collar services, 5 gyms if viable) with source URLs and screenshots. If inaccessible, record actual challenge and stop inventing statuses.
- [ ] M3: Deduplicate the 19 company prospects against actual BrightReach DB; audit booking paths. Record net-new qualified unique firms separately from raw listings. Add comparable gym prospects only after economics check.
- [ ] M4: Configure one offer and one funnel: client-funded Meta lead ad → intake → consent capture → dedupe → qualified scoring → booking request. Do not launch paid ads without the business's authorization and budget.
- [ ] M5: Repair n8n lead capture/qualification/follow-up/calendar workflows; verify one end-to-end fake prospect with actual webhook, dedupe, calendar event in authorized test calendar, owner alert and full audit history. Prevent double booking, unauthorized SMS/email, and false success.
- [ ] M6: Produce 3 specific outreach **drafts** to real qualified prospects with verified facts and one CTA. Do not call messages sent unless transport confirms.
- [ ] M7: Prepare a demonstrable working funnel and sample reporting with test data clearly labeled. Get one interested prospect response or documented outreach milestone; no fabricated customers/appointments.
- [ ] M8: End-of-run plain-English report: contacts qualified, verified active advertiser examples, drafts prepared, messages actually delivered, replies, appointments actually booked, tests passing, blockers. Every metric must cite a real source.

## Tracking rules
- This repo's linked GitHub issue is the durable milestone ledger; Cursor updates the issue checklist/comment with date, log/test/artifact link and provider-confirmed evidence after each milestone. Also keep a brief local progress file in runtime/task-files/ (not committed if sensitive).
- Maximum 3 workers; don't spend on new inference, scrapers, data brokers, or ad campaigns without explicit owner authorization. No new scheduled jobs.
- Cursor Agent on Jarvis can edit workspace files, run terminal commands, and control its browser. It does **not** have blanket unrestricted Windows desktop control; use local tools/MCP with scoped permissions. Recommended Cursor mode: Auto-review; require approval for credentials, sending/publishing, payments or destructive actions.
- Video research default: try public direct URL with yt-dlp; extract audio with FFmpeg; transcribe with local Whisper; verify transcript against source. If one method fails, try another supported media route rather than guessing text.
