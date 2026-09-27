from pathlib import Path

from cloudos.job_agent.models import Job
from cloudos.job_agent.ranking import score_job
from cloudos.job_agent.second_brain import ProfileReader


ROOT = Path(__file__).resolve().parents[1]


def test_priority_local_student_role_scores_and_selects_resume():
    profile = ProfileReader(ROOT / "data" / "applicant-profile.yaml").read()
    job = Job("LSU", "Student Office Assistant", "Baton Rouge, LA", "https://example.test/job", description="Part-time student employee using Microsoft Excel and customer service")
    score = score_job(job, profile)
    assert 0 <= score.total <= 100
    assert score.total >= 65
    assert score.resume_variant in {"general-student", "desk-office"}


def test_scam_is_rejected():
    profile = ProfileReader(ROOT / "data" / "applicant-profile.yaml").read()
    job = Job("Fake", "AI Assistant", "Remote", "https://example.test/job", description="Pay to join. Crypto investment. Unlimited earning potential.")
    score = score_job(job, profile)
    assert score.total == 0
    assert score.reject_reason == "low_quality_job"


def test_job_identity_is_stable_and_distinguishes_requisitions():
    one = Job("Company", "Assistant", "Baton Rouge", "https://example.test/a", ats="greenhouse", ats_job_id="123")
    same = Job(" company ", "Assistant", "Remote", "https://example.test/changed", ats="greenhouse", ats_job_id="123")
    other = Job("Company", "Assistant", "Baton Rouge", "https://example.test/a", ats="greenhouse", ats_job_id="456")
    assert one.job_id == same.job_id
    assert one.job_id != other.job_id


def test_senior_and_experience_heavy_roles_are_rejected():
    profile = ProfileReader(ROOT / "data" / "applicant-profile.yaml").read()
    lead = Job("xAI", "Lead Hardware Deployment Engineer", "Remote", "https://example.test/job")
    experienced = Job("Company", "AI Operations Specialist", "Remote", "https://example.test/job2", description="Minimum 5+ years of professional experience required")
    assert score_job(lead, profile).reject_reason == "clearly_underqualified"
    assert score_job(experienced, profile).reject_reason == "clearly_underqualified"


def test_unverified_language_or_portfolio_specialty_is_rejected():
    profile = ProfileReader(ROOT / "data" / "applicant-profile.yaml").read()
    language = Job("xAI", "AI Tutor - Spanish", "Remote", "https://example.test/job")
    video = Job("xAI", "Video Tutor", "Remote", "https://example.test/job2", description="Portfolio required. Strong Premiere Pro and VFX skills.")
    assert score_job(language, profile).reject_reason == "unknown_required_specialty"
    assert score_job(video, profile).reject_reason == "clearly_underqualified"
