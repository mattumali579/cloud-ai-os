from cloudos.job_agent.models import Job
from cloudos.job_agent.security import verify_legitimacy


def test_direct_https_ats_is_accepted():
    ok, reason = verify_legitimacy(Job("LSU", "Student Worker", "Baton Rouge", "https://lsu.wd1.myworkdayjobs.com/job/123"))
    assert ok
    assert reason == ""


def test_shortener_and_payment_language_are_rejected():
    assert not verify_legitimacy(Job("Recruiter", "Assistant", "Remote", "https://bit.ly/x"))[0]
    assert not verify_legitimacy(Job("Recruiter", "Assistant", "Remote", "https://example.com/job", description="Buy equipment with a gift card"))[0]
