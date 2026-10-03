# v3 copy experiment
Copy and rationale for the v3 A/B on this branch. This file does not send mail and does not write a database.
Drafted 2026-10-02 23:17 PT. Measurement queries: `experiments/v3_measure.sql` (read-only; not a migration).
The code described in section 6 is the implementation on this branch (originally `experiments/v3.patch` against `6f8bac1`).

## 1. Why test copy now
- Sends so far: about 388 (188 historical + about 200 Hostinger, Oct 1-2). Human replies: 0. Spam folder: empty. Bounces: 12.
- Mail-tester scores 9.5/10, with SPF/DKIM/DMARC passing and no blacklists. Technical deliverability is not the main suspect. Message, offer and targeting are.
- What v2 does today (src/cloudos/outreach/copy.py, COPY_VERSION "v2"):
  - Bodies run 120-170 words.
  - The pitch is abstract: "reach back out to those people... without new ad spend".
  - It goes to all 22 industries with the same structure.
  - The CTA is open-ended: "Want me to send over how that would work for {Name}?"
  - It is signed with only the SENDER_NAME secret, with no business name. The fitnesshubb.com address gives the reader no clue who "Matt" is.
  - Touch 2 asks "Is getting more {new} a priority...?" and adds no new reason to reply.
- Timing: v2 touch 2 starts Tue Oct 6 (Oct 1 sends) and Wed Oct 7 (Oct 2 sends). Those leads stay on v2. The patch never changes rows that were already sent or attempted.

## 2. Hypothesis and design
H1: for HVAC, plumbing and roofing companies, a short email with one concrete outcome, a yes/no CTA and a named business gets more non-opt-out human replies than v2. Touch 2 adds the existing price plus the existing 30-day money-back guarantee.

This is a package test (v3 changes length, outcome, CTA, signature and touch 2 together), not a one-variable test. At 0/388 the first question is "does anything get replies", and single-variable tests at this volume would take months.

| | v2 (control) | v3 (challenger) |
|---|---|---|
| Length (greeting to signature) | 120-170 words | 68-84 words (QA rejects anything over 90) |
| Outcome | "turn old leads into booked jobs" (vague) | "every missed call gets a text back within a minute" / "every estimate gets a same-day follow-up" |
| CTA | "Want me to send over how that would work...? A one-word reply is fine." | "Worth a short outline of how it would work for you? A yes or no is fine." |
| Signature | SENDER_NAME | SENDER_NAME + "BrightReach Media" |
| Touch 2 | priority question, no new info | $1,500 one time, you own it, 30-day work-free-or-refund guarantee (all from config) |
| Touch 3 | soft breakup | soft breakup, reply "outline" |
| Who | all 22 industries | hvac, plumbing, roofing only. Inside those industries a stable 50/50 split by company_id hash. |
| Kept the same | plain text, no links, no tracking, postal address, "stop" opt-out, List-Unsubscribe header, technology-word ban, threading, pacing, caps | same |

## 3. Niche recommendation: HVAC, plumbing, roofing (electrical as the alternate)
Why these:
- **The offer is trade-shaped.** config/brightreach_offers.yaml sells a "Missed-Call Revenue Recovery Build" (text back missed calls, book jobs, same-day quote follow-ups), and the lead pack is literally "trade leads".
- **Matt's plan was trades from the start.** MONEY_OPERATOR_STATE's intended source is roofing/HVAC/plumbing/electrical, Louisiana-first.
- **Losing a missed call costs a trade a job.** Emergency trades lose a job to the next number on the list. For gyms, salons or law firms a missed call is a weaker pain.
- **The scraped facts fit trades.** The two trade-specific facts the lead engine records, `emergency_service` and `free_estimate_offer`, drive v2's most specific angles and v3's two variants.
- **The best contact data sorts first.** Qualification (leadgen/qualify.py) marks a company HIGH only with a validated email on its own domain plus 2 or more public facts, and the planner sends HIGH first.

Not verified:
- **Per-industry contact-data counts and the Oct 1-2 industry mix live only in the leads DB.** Logs and committed reports carry no per-industry numbers. Run `v3_measure.sql` Q1 (contact quality by industry) and Q2 (what was sent, by industry and variant) before turning v3 on. If electrical has clearly more HIGH plus own-domain contacts than one of the three, swap it in (v3 already has copy for electrical).

Why not gyms (even though fitnesshubb.com fits gyms):
- The missed-call/estimate offer is weak for gyms.
- The only gym attempt (10 manual Sep 24 emails, $60 per intro) got 0 replies. That sample is tiny, but it isn't a signal to chase.
- A gym offer would need new pricing that isn't in config.

Inventory: 1,579 outreach-ready after the 22:56 PT Lead Engine run. The share that is trades is unknown (Q1 answers it).

Optional narrowing knob (in the patch, off by default): `planning.industry_limit.enabled: true` (industries `[hvac, plumbing, roofing]`) makes the planner prepare new first touches only for those industries.
- Recommended ON if Q1 shows at least 300 ready trade companies. Otherwise leave it at `[]` and compare only within trades.
- About 400 first touches already queued (all industries; 353 of them passed the send checks at the Oct 1 audit) drain first because of claim order. Trade rows among them are re-written into the two arms at the next plan run. Non-trade rows stay v2 and are left out of the comparison.
- Cancelling the queued non-trade rows would speed the test. That is a live DB write, so it is NOT in the patch and needs Matt's explicit OK.

## 4. The copy (rendered by the patched code; example company "Bayou Plumbing Co.", plumbing)
### First touch, variant v3-missed (no estimate fact; emergency fact adds the opener line)
Subject: `Missed calls at {Name}` (or `Missed calls` if the name is over 28 characters)
```
Hi Bayou Plumbing team,

When a call to Bayou Plumbing goes to voicemail, most people just call the next plumber on the list.

I set it up so every missed call gets a text back within a minute, so more of those callers book with you instead.

Worth a short outline of how it would work for you? A yes or no is fine.

Matt Umali
BrightReach Media

--
{SENDER_POSTAL_ADDRESS}
If you'd rather not hear from me, reply "stop" and I won't email you again.
```
68 words. With an emergency fact the first paragraph becomes "I saw {Name} takes emergency calls. When one of those goes to voicemail, most people just call the next {plumber|HVAC company|roofer} on the list." (74 words).

### First touch, variant v3-estimates (site advertises free estimates)
Subject: `Estimates at {Name}` (or `Quiet estimates`)
```
Hi Bayou Plumbing team,

I saw Bayou Plumbing offers free estimates. Most estimates that end with "let me think about it" never get a second follow-up, and the job goes to whoever checks back first.

I make sure every estimate gets a same-day follow-up until it's a clear yes or no.

Worth a short outline of how it would work for you? A yes or no is fine.

Matt Umali
BrightReach Media

--
{SENDER_POSTAL_ADDRESS}
If you'd rather not hear from me, reply "stop" and I won't email you again.
```
72 words.

### Touch 2 (v3-fu1, 3 business days later, threaded as `Re: {first subject}`)
```
Hi Bayou Plumbing team,

Quick follow-up. It's one build that texts back every missed call and follows up every estimate the same day - $1,500 one time, and you own it.

If it isn't texting back missed calls and booking jobs within 30 days of going live, I keep working free until it does, or you get your money back.

Want the one-page outline for Bayou Plumbing? A yes or no is fine.

Matt Umali
BrightReach Media

--
{SENDER_POSTAL_ADDRESS}
Reply "stop" and you won't hear from me again.
```
77 words.

### Touch 3 (v3-fu2, 5 business days after touch 2)
```
Hi Bayou Plumbing team,

Last note from me. If missed calls or quiet estimates ever cost Bayou Plumbing a job, reply "outline" and I'll send it over.

Matt Umali
BrightReach Media
--  (same footer)
```

### Where every claim comes from (nothing new invented)
| v3 wording | Source in config/brightreach_offers.yaml (recovery_build) |
|---|---|
| "every missed call gets a text back within a minute" | deliverable "Instant text-back on every missed call". "Within a minute" is my reading of "instant"; **Matt to confirm he can deliver it** |
| "every estimate gets a same-day follow-up" | deliverable "Same-day automated quote follow-ups" ("automated" is left out because the technology words are banned) |
| "$1,500 one time" | setup_usd 1500 / price_text "$1,500 one-time" |
| "you own it" | deliverable "Runs 24/7; you own it" |
| 30-day guarantee | guarantee string, verbatim except "go-live" became "going live" |
| "one-page outline" | close-kit OFFER_ONE_PAGER.md (the source the offers file names) |

Not used in cold email:
- **$400/mo hosted.** It's optional, and the reply layer (conversations/actions.py) already offers it after a reply.
- **The $150-200 lead pack.** "25 exclusive trade leads" has no fulfillment path in the code (the lead engine finds businesses, not homeowners), so leading with it risks promising something that can't be delivered.
- **The $60-per-attended-intro gym offer.** It isn't in config.

What happens after a v3 reply: the reply layer quotes the same $1,500 + $400 + guarantee, so the story stays consistent, unlike the gym emails.

## 5. Sender identity and the fitnesshubb.com mismatch (no new domain)
- **From display name = "Matt Umali"**, set in the `SENDER_NAME` GitHub secret. It is masked today and the code default is "Matt". A person's name matches `matt@`. A brand name in the From line ("BrightReach Media <matt@fitnesshubb.com>") shows the mismatch in the inbox list before anyone opens the email.
- **Signature = "Matt Umali" / "BrightReach Media".** v3 adds the brand line in code and skips it if SENDER_NAME already contains it, so it never doubles.
- **Don't mention fitnesshubb.com in the body.** If a prospect asks, the honest one-liner is: "fitnesshubb.com is my own site; BrightReach Media is the name I use for this work."
- **Compliance stays intact.** The From line names the real person and a real mailbox (an accurate sender identity), and the postal address, "stop" opt-out and List-Unsubscribe are unchanged.
- **Separate improvement, deliberately not in this patch** (to keep the test clean): add an https one-click List-Unsubscribe plus List-Unsubscribe-Post (see evidence/07).

## 6. Patch summary (`experiments/v3.patch`, base 6f8bac1, +140/-18 lines, 4 files, NOT applied)
- `config/outreach_sender.yaml` adds:
  - `experiment.v3_share: 0.5`
  - `experiment.v3_industries: [hvac, plumbing, roofing]`
  - `planning.industry_limit` (optional narrowing; `enabled: false` by default, industries `[hvac, plumbing, roofing]`, electrical is the alternate). Rev 2 also sets `sender.from_name_default: Matt Umali`
- `copy.py`:
  - `arm()`: the arm is set by sha256(company_id), so a lead never flips between arms. Only v3 industries can get v3.
  - `first_touch(..., version=)` routes to `first_touch_v3()` (variants `v3-missed` / `v3-estimates`).
  - `followup(..., version=)` writes v3 touch 2/3 (`v3-fu1` / `v3-fu2`).
  - `qa()` adds a "too long for v3" rule (over 90 words above the footer).
  - v2 text and labels are unchanged.
- `sender.py`:
  - `copy_arm()` reads the config.
  - `plan()` writes each new first touch in its arm.
  - `refresh_queued()` re-writes queued, never-attempted first touches whose label doesn't match their arm. That also covers turning v3 off: setting share 0 re-writes them back to v2.
  - `record_sent()` builds follow-ups in the first touch's arm.
  - `_candidates()` respects the optional `planning.industry_limit` (only when enabled).
- `tests/test_outreach_sender.py`:
  - Existing tests pin share 0.
  - 6 new tests cover v3 QA, ≤90 words, one question, brand, no $ or links in touch 1, a deterministic ~50% split, industry gating, and follow-ups staying in the v3 arm.
- **Verified on the box** (local clone + throwaway Postgres 17; nothing pushed): `git apply --check` is clean. test_outreach_sender went from 40 passed to 46 passed. The full suite has the same 5 failed / 13 errors as baseline, all in unrelated tests (test_job_agent_safety etc.).
- **No migration needed.** `outreach_queue.copy_variant` and `outreach_messages.copy_variant` already exist and already store the label (the send path passes `copy_variant` into `store.record_confirmed_send`).

## 7. How replies are attributed to each arm, and how the winner is decided
- **Attribution:**
  - A company's arm is the prefix of its step-0 `outreach_queue.copy_variant` (`v2-*` or `v3-*`). Follow-ups inherit it (`v3-fu1`/`v3-fu2`; v2 follow-ups keep `fu1`/`fu2`).
  - A reply is an `outreach_messages` row with direction 'inbound', kind not in ('auto_reply','bounce'), the same company_id, and timestamp after the first touch. The existing reply poller already writes these with company_id.
  - The touch that earned the reply is the latest touch sent before it (step 1/2 `sent_at`).
  - `v3_measure.sql` Q3 outputs per arm: first_touches, human_replies, replies_not_optout, positive (interested / meeting_requested / proposal_* / negotiating / won), opted_out, bounced, and replies after touch 1/2/3. Set its start date to the day v3 goes live.
- **Primary metric:** non-opt-out human reply rate, any touch, trades only, both arms in the same window.
- **Guardrails:** pause v3 (`v3_share: 0`) if its opt-out rate is over 3% of sends, its bounce rate is clearly above v2's, or Hostinger sends any warning or complaint.
- **Readout:** once each arm has at least 150 trade first touches AND 10 calendar days have passed since the 150th (enough for touch 2).
  - v3 wins if it has 3 or more non-opt-out replies and at least 2x v2's.
  - Then: make v3 the default for trades and write the next challenger.
  - At this volume the test only detects big differences. For example, 4/150 vs 0/150 has a one-sided Fisher p ≈ 0.06. Treat it as directional.
  - Any positive reply goes to Matt the same day, whichever arm it came from.
- **Capacity reality:**
  - The 100/day cap includes follow-ups, and follow-ups claim first.
  - About 200 v2 touch-2s land Oct 6-7, and their touch-3s land about Oct 13-14. Every new first touch adds a touch 2 and a touch 3, so the steady state is only about 33 new first touches per sending day.
  - The planner keeps about 400 first touches queued (queue_ahead 400, all industries), and they claim in queue order. Trade rows already in that queue join the test right away, after being re-written into the arms. Non-trade rows only add delay.
  - Rough timing: 300 trade first touches (150 per arm) take about 3-4 weeks if nothing changes, so the readout lands around early-to-mid November PT.
  - Narrowing ON plus cancelling the queued, never-attempted non-trade first touches cuts this to about 9-10 sending days plus the 10-day window (readout about Oct 26-30 PT). The cancel is a live DB write and needs Matt's explicit OK:
    `UPDATE outreach_queue q SET state='cancelled', stop_reason='v3 test: trades only', updated_at=now() FROM companies c WHERE c.company_id=q.company_id AND q.step=0 AND q.state='queued' AND q.attempts=0 AND q.message_id_header IS NULL AND c.industry <> ALL (ARRAY['hvac','plumbing','roofing']);`
    The cancelled companies stay un-contacted. They are not re-planned while their cancelled queue row exists, because `_candidates` skips any company with a queue row.

## 8. Steps if Matt approves (none done)
1. Run `v3_measure.sql` Q1 and Q2 (read-only) and confirm or swap the three industries.
2. Set the `SENDER_NAME` secret to "Matt Umali" and confirm `SENDER_POSTAL_ADDRESS`.
3. Confirm "text back within a minute" and "same-day estimate follow-up" can actually be delivered. Have the one-page outline ready to send.
4. Apply `v3.patch` on a branch, let Outreach Tests run, then merge. The next Outreach Send plan step re-writes queued trade rows into the arms.
5. Optionally set `planning.industry_limit.enabled: true` (industries `[hvac, plumbing, roofing]`).
6. Run Q3 every couple of days. Read out using the rule above.

## 9. Risks / open questions
- **v3 claims a specific capability** (text-back within a minute). If it isn't built and deliverable, don't send v3.
- **The price in touch 2 may lower replies** from price-sensitive owners, but it also qualifies them. Replies after touch 2 are measured separately.
- **"you" in the CTA** where v2 repeated the company name: a deliberate cut to stay under 90 words with long names. The longest test name came to 84 words.
- **v2 and v3 both still carry the X-Leadgen-* headers.** They're harmless but fingerprintable. They're unchanged here so the arms stay comparable.
