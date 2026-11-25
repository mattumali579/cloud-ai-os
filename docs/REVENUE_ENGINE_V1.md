# BrightReach Revenue Engine V1

## Objective
Build one simple client-acquisition and fulfillment system that sells an emotionally compelling end-state, not "AI services."

## Chosen niche
Residential roofing contractors in Louisiana / Gulf South.

Ideal client:
- 5-50 employees
- residential reroof / storm / insurance work
- enough crew capacity to take more jobs
- active website + Google Business Profile
- weak/inconsistent paid social or slow lead follow-up
- owner/operator can make a buying decision quickly

## The endgame we sell
**"Wake up to qualified homeowners already booked on your calendar for roof inspections, without chasing Angi leads, begging for referrals, or babysitting your phone."**

The emotion is control + relief + momentum. The measurable outcome is qualified booked roof-inspection appointments.

Do not sell "AI ads," "automation," "Higgsfield," "n8n," or "agents" as the headline. Those are mechanisms.

## Offer
Name: **Booked Roof Estimate Engine**

What the client gets:
- local homeowner offer + angle research
- 3-5 direct-response ad creatives
- Facebook/Instagram lead capture
- qualification form
- instant follow-up
- calendar booking
- missed-lead follow-up
- daily owner report
- weekly creative/offer iteration

Founding-client commercial model:
- $500 one-time launch fee
- client pays ad spend directly
- $100 per qualified booked roof inspection
- month-to-month, no long contract
- delivery guarantee: if the system is not live and booking-ready within 7 days after receiving required access/assets, refund the launch fee

Do not guarantee revenue or a fixed number of jobs before evidence exists.

## Simple business loop

OUTBOUND TO ROOFERS
  -> show the end-state
  -> short personalized audit/demo
  -> close founding client
  -> BUILD ENGINE
      research
      -> offer/creative brief
      -> Higgsfield + static creatives
      -> QA
      -> Meta lead capture
      -> qualification
      -> follow-up
      -> calendar booking
      -> daily report
      -> weekly iteration
  -> collect proof
  -> turn proof into content/case study
  -> outbound gets easier
  -> raise price

Pause the affiliate branch until the first paying client and case study exist.

## Existing stack to reuse
- cloud-ai-os: orchestration, state, scheduled jobs, reporting
- Airtable: prospects, outreach state, clients, leads, appointments
- Gmail: outreach/replies once dedicated senders exist
- n8n: deterministic workflow glue
- Higgsfield: ad creative generation once connected
- FFmpeg/content QA system: creative assembly and verifier
- GitHub Actions / always-on worker: scheduled jobs
- existing research/knowledge workflows: niche research, hooks, pain/desire extraction

## Build order
1. Offer + ICP + qualification rubric.
2. Roofing prospect list + scoring.
3. Personalized outbound generator.
4. One-page audit/demo generator.
5. Client onboarding checklist.
6. Creative brief generator.
7. Lead capture + qualification + booking workflow.
8. Daily owner report.
9. Evidence ledger: SENT means provider-confirmed sent; BOOKED means calendar-confirmed.
10. Case-study generator after real results.

## Hard rules
- No fake success states.
- Drafted is not sent.
- Generated lead is not verified lead.
- Form fill is not booked appointment.
- Do not claim revenue unless client/source data proves it.
- Every production action writes evidence.
- Keep recurring cost at $0 for our internal stack where practical; client ad/tool spend is separate.
- Build the minimum system that can close and fulfill one client before scaling to 500 emails/day.

---

# MASTER BUILD PROMPT

You are the primary operator for BrightReach Revenue Engine V1 inside the existing `cloud-ai-os` repository.

Your job is not to give me plans. Your job is to inspect the existing repo, reuse what already works, and build the minimum production-ready system for the Booked Roof Estimate Engine.

BUSINESS:
We sell residential roofing contractors the end-state: "Wake up to qualified homeowners already booked on your calendar for roof inspections, without chasing Angi leads, begging for referrals, or babysitting your phone."

Do not market AI as the product. AI/Higgsfield/n8n/Airtable/Gmail are mechanisms.

OFFER:
- $500 launch
- client funds ad spend
- $100 per qualified booked roof inspection
- month-to-month
- launch-fee refund if system is not live and booking-ready within 7 days after required access/assets are supplied
- never guarantee revenue

EXECUTION ORDER:
1. Audit the repo and identify reusable components.
2. Create a concise implementation plan tied to existing files/modules.
3. Build the ICP + roofer qualification/scoring rules.
4. Build the prospect/outreach data model.
5. Build personalized outreach generation around the END STATE, not features.
6. Build a one-page audit/demo artifact generator for each prospect.
7. Build client onboarding data structures/checklist.
8. Build the creative-brief pipeline for Meta ads and Higgsfield.
9. Build lead intake -> qualification -> follow-up -> booking state machine.
10. Build daily owner reporting.
11. Build an evidence ledger so no action can be reported complete without provider evidence.
12. Add tests.
13. Run tests and fix failures.
14. Write a short operator runbook.

INTEGRATIONS:
Reuse Airtable, Gmail, n8n, GitHub Actions/worker, existing research pipeline, FFmpeg/creative QA, and existing cloud-ai-os infrastructure where present. If an integration is unavailable, build the adapter/interface and mark the exact external credential/access blocker. Do not fabricate successful connections.

AUTONOMY:
Do not stop to ask me minor questions. Use best judgment. Keep moving until blocked by a true external requirement such as OAuth, CAPTCHA, payment, or missing credentials. When blocked, continue every independent task and leave one precise blocker with the exact human action required.

SUCCESS GATE:
The build is only "done" when:
- tests pass,
- the repo contains runnable workflows,
- the offer/ICP/outreach/fulfillment assets exist,
- evidence states are fail-closed,
- no drafts/skips are counted as sends,
- no unverified lead is counted as qualified,
- no form fill is counted as a booked appointment,
- and a dry-run can trace one roofer from prospect -> outreach payload -> client onboarding -> ad brief -> lead -> qualified -> booked -> owner report.

Do not optimize for architecture elegance. Optimize for first paying client, proof, and repeatability.
