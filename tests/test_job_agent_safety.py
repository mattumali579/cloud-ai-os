from pathlib import Path

import pytest

from cloudos.config import Settings
from cloudos.job_agent.answers import AnswerBank, QuestionClassifier
from cloudos.job_agent.ats.forms import inspect_html


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "ats"


@pytest.fixture
def classifier():
    return QuestionClassifier(AnswerBank(ROOT / "data" / "application_answers.yaml"))


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ('<form><label for="x">Social Security Number</label><input id="x" required></form>', "sensitive_information_requested"),
        ('<form><label for="x">Government-issued ID number</label><input id="x" required></form>', "sensitive_information_requested"),
        ('<form><label for="x">Bank account and routing number</label><input id="x" required></form>', "sensitive_information_requested"),
        ('<p>Complete the CAPTCHA challenge</p>', "captcha"),
        ('<p>Complete this cognitive assessment</p>', "assessment_required"),
        ('<form><label for="x">Favorite management philosophy</label><input id="x" required></form>', "unknown_answer"),
        ('<p>Sign in required to continue</p>', "authentication_required"),
    ],
)
def test_prohibited_and_unknown_forms_are_skipped(classifier, text, reason):
    result = inspect_html(text, classifier, Settings())
    assert result.allowed is False
    assert result.skip_reason == reason


@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        ("greenhouse.html", "sensitive_information_requested"),
        ("workday.html", "authentication_required"),
        ("generic.html", "assessment_required"),
    ],
)
def test_ats_style_fixtures_fail_closed(classifier, fixture, reason):
    result = inspect_html((FIXTURES / fixture).read_text(encoding="utf-8"), classifier, Settings())
    assert not result.allowed
    assert result.skip_reason == reason


def test_safe_lever_fixture_is_eligible(classifier):
    result = inspect_html((FIXTURES / "lever.html").read_text(encoding="utf-8"), classifier, Settings())
    assert result.allowed
    assert {q["kind"] for q in result.questions} == {"SAFE_STANDARD"}


def test_excessive_custom_questions_are_skipped(classifier):
    fields = "".join(f'<label for="q{i}">Describe example {i}</label><textarea id="q{i}" required></textarea>' for i in range(6))
    result = inspect_html(f"<form>{fields}</form>", classifier, Settings(max_custom_questions=5))
    assert not result.allowed
    assert result.skip_reason == "application_too_long"


def test_contextual_name_field_is_not_filled_with_applicant_name(classifier):
    result = inspect_html('<form><label for="x">Former employer name</label><input id="x" required></form>', classifier, Settings())
    assert not result.allowed
    assert result.skip_reason == "unknown_answer"
