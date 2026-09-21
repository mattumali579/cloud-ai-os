from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cloudos.config import Settings, get_settings

from .answers import AnswerBank
from .ats.factory import adapter_for
from .discovery import DiscoveryService
from .discord import JobNotifier
from .models import Job, JobStatus, SkipReason
from .ranking import score_job
from .resumes import ResumeGenerator, select_resume_variant
from .second_brain import ProfileReader
from .security import verify_legitimacy
from .tracker import PostgresJobStore

log = logging.getLogger("cloudos.job_agent")


class JobAgent:
    def __init__(self, conn: Any, settings: Settings | None = None, discovery: DiscoveryService | None = None):
        self.settings = settings or get_settings()
        self.conn = conn
        self.store = PostgresJobStore(conn)
        self.profile = ProfileReader(self.settings.job_profile_path).read()
        self.answers = AnswerBank(self.settings.job_answers_path)
        self.discovery = discovery or DiscoveryService(self.settings)
        self.resumes = ResumeGenerator(self.profile, Path(self.settings.job_profile_path).parents[1] / "resumes")
        self.notifier = JobNotifier(conn)

    def discover_jobs(self) -> dict:
        found = self.discovery.discover()
        counts = {"discovered": 0, "qualified": 0, "rejected": 0, "duplicate": 0, "failed": 0}
        qualified: list[Job] = []
        for job in found.jobs:
            try:
                legitimate, legitimacy_reason = verify_legitimacy(job)
                score = score_job(job, self.profile)
                job.job_score = score.total
                job.score_detail = score.detail
                job.category = score.category
                job.resume_variant = select_resume_variant(score.category)
                if not legitimate or score.reject_reason or score.total < self.settings.job_min_score:
                    job.status = JobStatus.REJECTED
                    job.skip_reason = legitimacy_reason or score.reject_reason or SkipReason.LOW_QUALITY_JOB.value
                else:
                    job.status = JobStatus.QUALIFIED
                _, inserted = self.store.upsert_discovered(job)
                if not inserted:
                    counts["duplicate"] += 1
                    continue
                counts["discovered"] += 1
                counts[job.status.value] += 1
                if job.status == JobStatus.QUALIFIED:
                    qualified.append(job)
            except Exception as exc:  # one malformed job never ends discovery
                counts["failed"] += 1
                found.errors.append({"source": job.source, "error": f"{type(exc).__name__}: {exc}"[:300]})
        return {"counts": counts, "errors": found.errors, "qualified": qualified}

    def apply_jobs(self, dry_run: bool | None = None) -> dict:
        dry = self.settings.dry_run if dry_run is None else dry_run
        self.resumes.generate_all()
        jobs = self.store.qualified(self.settings.max_applications_per_run)
        counts = {"qualified": len(jobs), "submitted": 0, "skipped": 0, "failed": 0, "would_submit": 0}
        results = []
        for job in jobs:
            try:
                result = self._apply_one(job, dry)
                results.append({"job_id": job.job_id, "job": job.title, "company": job.company, "score": job.job_score, "resume": job.resume_variant, "ats": result.ats, "status": result.status, "reason": result.skip_reason, "questions": result.questions})
                counts[result.status] = counts.get(result.status, 0) + 1
            except Exception as exc:  # hard batch boundary
                counts["failed"] += 1
                if not dry:
                    self.store.transition(job.job_id, JobStatus.FAILED, last_attempt=_now(), attempt_count=job.attempt_count + 1)
                results.append({"job_id": job.job_id, "job": job.title, "company": job.company, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:300]})
                log.exception("application %s failed; continuing", job.job_id)
        return {"dry_run": dry, "counts": counts, "results": results, "top": jobs[:3]}

    def _apply_one(self, job: Job, dry_run: bool):
        variant = job.resume_variant or select_resume_variant(job.category)
        master = self.resumes.output_dir / __import__("cloudos.job_agent.resumes.generator", fromlist=["VARIANTS"]).VARIANTS[variant]
        resume = self.resumes.generate(variant, job) if job.job_score >= 80 else master
        job.resume_variant = variant
        job.tailored_resume_path = str(resume) if job.job_score >= 80 else ""
        if not dry_run:
            self.store.transition(job.job_id, JobStatus.APPLYING, last_attempt=_now(), attempt_count=job.attempt_count + 1, resume_variant=variant, tailored_resume_path=job.tailored_resume_path)
        adapter = adapter_for(job.ats, self.settings, self.profile, self.answers)
        result = adapter.apply(job, resume, dry_run)
        if dry_run:
            return result
        if result.status == "submitted":
            job.status = JobStatus.SUBMITTED
            job.applied_at = _now()
            job.confirmation_id = result.confirmation_id
            job.confirmation_text = result.confirmation_text
            self.store.transition(job.job_id, JobStatus.SUBMITTED, applied_at=job.applied_at, confirmation_id=job.confirmation_id, confirmation_text=job.confirmation_text, resume_variant=variant, tailored_resume_path=job.tailored_resume_path)
            self.notifier.submitted(job)
        elif result.status == "skipped":
            job.status = JobStatus.SKIPPED
            job.skip_reason = result.skip_reason
            self.store.transition(job.job_id, JobStatus.SKIPPED, skip_reason=job.skip_reason, last_attempt=_now(), application_complexity=len(result.questions), resume_variant=variant)
            self.notifier.skipped(job, result.detail)
        else:
            self.store.transition(job.job_id, JobStatus.FAILED, last_attempt=_now(), resume_variant=variant)
        return result

    def run(self, dry_run: bool | None = None) -> dict:
        dry = self.settings.dry_run if dry_run is None else dry_run
        run_id = self.store.start_run("run", dry)
        errors: list[dict] = []
        try:
            discovered = self.discover_jobs()
            errors.extend(discovered["errors"])
            applied = self.apply_jobs(dry)
            counts = {**discovered["counts"]}
            for key, value in applied["counts"].items():
                if key != "qualified":
                    counts[key] = counts.get(key, 0) + value
            if not dry:
                db_counts = self.store.daily_counts()
                self.notifier.daily_report(db_counts, applied["top"])
            self.store.finish_run(run_id, counts, errors)
            return {"run_id": run_id, "dry_run": dry, "counts": counts, "errors": errors, "applications": applied["results"]}
        except Exception as exc:
            errors.append({"source": "run", "error": f"{type(exc).__name__}: {exc}"[:300]})
            self.store.finish_run(run_id, {}, errors)
            raise

    def report(self) -> dict:
        counts = self.store.daily_counts()
        top = [Job.from_mapping(dict(row)) for row in self.store.list(JobStatus.SUBMITTED.value, 3)]
        self.notifier.daily_report(counts, top)
        return {"counts": counts, "top": [job.to_dict() for job in top]}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
