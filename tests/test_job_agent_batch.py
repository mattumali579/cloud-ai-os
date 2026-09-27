from types import SimpleNamespace

from cloudos.job_agent.models import Job, JobStatus
from cloudos.job_agent.orchestrator import JobAgent


class FakeStore:
    def __init__(self, jobs):
        self.jobs = jobs
        self.transitions = []

    def qualified(self, limit):
        return self.jobs[:limit]

    def transition(self, *args, **kwargs):
        self.transitions.append((args, kwargs))


def test_broken_application_does_not_terminate_run(monkeypatch):
    first = Job("Broken Co", "Office Assistant", "Baton Rouge", "https://example.test/1", status=JobStatus.QUALIFIED, job_score=80, resume_variant="desk-office")
    second = Job("Good Co", "Student Worker", "LSU", "https://example.test/2", status=JobStatus.QUALIFIED, job_score=85, resume_variant="general-student")
    agent = JobAgent.__new__(JobAgent)
    agent.settings = SimpleNamespace(dry_run=True, max_applications_per_run=10)
    agent.store = FakeStore([first, second])
    agent.resumes = SimpleNamespace(generate_all=lambda: None)
    called = []

    def fake_apply(job, dry):
        called.append(job.company)
        if job.company == "Broken Co":
            raise RuntimeError("fixture failure")
        return SimpleNamespace(status="would_submit", ats="generic", skip_reason="", questions=[])

    monkeypatch.setattr(agent, "_apply_one", fake_apply)
    result = agent.apply_jobs(dry_run=True)
    assert called == ["Broken Co", "Good Co"]
    assert result["counts"]["failed"] == 1
    assert result["counts"]["would_submit"] == 1
