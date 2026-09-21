# Autonomous Job Agent

The AI Second Brain remains the canonical knowledge source. The runtime lives
entirely in Cloud AI OS and consumes a provenance-carrying export at
`data/applicant-profile.yaml`. Both that file and generated PDFs are ignored by
Git because they contain private applicant information.

## Safety contract

- Every answer is `VERIFIED` or `UNKNOWN`; required unknowns skip the job.
- SSN, government ID, passport, banking, credit card, medical, tax, and early
  background-check fields always skip before submission.
- CAPTCHA, expired auth, assessments, recorded/video interviews, long forms,
  and more than five custom questions skip only that application.
- `DRY_RUN=true` executes discovery, scoring, browser inspection, resume choice,
  field classification, and validation but suppresses the final submit click.
- A job is submitted only after a visible confirmation is observed. An
  unverified post-click result is marked failed and is not automatically retried.
- History uniqueness uses a stable company/title/ATS identifier plus unique ATS
  job IDs. Submitted and failed rows are not returned to the apply queue.

## Runtime

The existing API accepts `job.agent.run`; the existing worker dispatches it;
the browser-capable worker image uses Debian Chromium on both amd64 and arm64;
the existing Postgres/Supabase database stores history; the existing owner
notification webhook fans out to Discord; and `n8n/job_agent_schedule.json`
provides three reasonable weekday runs.

The schedule ships inactive. It must not be activated until the Oracle host has
the private profile/answers files, any legitimate browser sessions have been
created on that host, the Discord webhook path is live, and one dry run passes.
This is an authorization/deployment gate, not application logic.

## CLI

```text
job-agent run
job-agent discover
job-agent apply
job-agent status
job-agent history
job-agent skipped
job-agent resumes
job-agent resume-test
job-agent dry-run
job-agent report
```
