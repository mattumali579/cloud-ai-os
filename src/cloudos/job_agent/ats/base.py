from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from cloudos.config import Settings

from ..answers import AnswerBank, QuestionClassifier
from ..models import Job, QuestionKind, SkipReason
from ..policy import complexity_decision, inspect_page_text
from ..second_brain.profile_reader import ApplicantProfile

log = logging.getLogger("cloudos.job_agent.ats")


@dataclass
class ApplicationResult:
    status: str
    skip_reason: str = ""
    detail: str = ""
    confirmation_id: str = ""
    confirmation_text: str = ""
    questions: list[dict] = field(default_factory=list)
    ats: str = "generic"
    would_submit: bool = False


class BaseATSAdapter:
    name = "generic"

    def __init__(self, settings: Settings, profile: ApplicantProfile, answers: AnswerBank):
        self.settings = settings
        self.profile = profile
        self.answers = answers
        self.classifier = QuestionClassifier(answers)

    def apply(self, job: Job, resume_path: str | Path, dry_run: bool) -> ApplicationResult:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        target = job.application_url or job.job_url
        if not target:
            return self._skip(SkipReason.UNSUPPORTED_ATS, "missing application URL")
        started = time.monotonic()
        state_dir = Path(self.settings.job_browser_state_path) / self.name
        state_dir.mkdir(parents=True, exist_ok=True)
        try:
            state_dir.chmod(0o700)
        except OSError:
            pass
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(state_dir), headless=True, accept_downloads=False,
                    executable_path=self.settings.job_browser_executable_path or None,
                    args=["--disable-dev-shm-usage", "--no-sandbox"],
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.set_default_timeout(min(30_000, self.settings.application_timeout_seconds * 1000))
                    page.goto(target, wait_until="domcontentloaded", timeout=45_000)
                    page = self._open_application_form(page)
                    result = self._inspect_fill_submit(page, job, Path(resume_path), dry_run, started)
                finally:
                    context.close()
                return result
        except PlaywrightTimeout:
            return self._skip(SkipReason.TIMEOUT, "application exceeded browser timeout")
        except Exception as exc:  # reset happens because context scope closes
            log.warning("%s application failed for %s: %s", self.name, job.job_id, type(exc).__name__)
            return ApplicationResult(status="failed", detail=f"{type(exc).__name__}: {exc}"[:300], ats=self.name)

    def _open_application_form(self, page):
        """Enter the application flow without crossing a submit/auth challenge."""
        if page.locator("input[type='file']:visible").count():
            return page
        apply_action = page.get_by_role("button", name=re.compile(r"^(apply|apply now|apply for this job)$", re.I))
        try:
            apply_action.first.wait_for(state="visible", timeout=10_000)
        except Exception:
            apply_action = page.get_by_role("link", name=re.compile(r"^(apply|apply now|apply for this job)$", re.I))
            try:
                apply_action.first.wait_for(state="visible", timeout=5_000)
            except Exception:
                return page
        if apply_action.count():
            existing = set(page.context.pages)
            apply_action.first.click()
            page.wait_for_timeout(1500)
            opened = [candidate for candidate in page.context.pages if candidate not in existing]
            if opened:
                page = opened[-1]
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20_000)
            except Exception:
                pass
        return page

    def _inspect_fill_submit(self, page, job: Job, resume_path: Path, dry_run: bool, started: float) -> ApplicationResult:
        body_text = page.locator("body").inner_text(timeout=15_000)
        challenge = page.locator("iframe[src*='recaptcha']:visible, iframe[src*='hcaptcha']:visible, iframe[title*='challenge' i]:visible")
        if challenge.count():
            return self._skip(SkipReason.CAPTCHA, "visible CAPTCHA challenge blocks progress")
        decision = inspect_page_text(body_text, self.settings)
        if not decision.allowed:
            return self._skip(decision.reason, decision.detail)
        if time.monotonic() - started > self.settings.application_timeout_seconds:
            return self._skip(SkipReason.TIMEOUT, "application exceeded configured time")

        fields = page.locator("input:visible, textarea:visible, select:visible")
        questions: list[dict] = []
        custom = 0
        for index in range(fields.count()):
            field = fields.nth(index)
            typ = (field.get_attribute("type") or field.evaluate("el => el.tagName.toLowerCase()") or "text").lower()
            if typ in {"hidden", "submit", "button", "reset"}:
                continue
            required = field.get_attribute("required") is not None or field.get_attribute("aria-required") == "true"
            label = self._label(page, field)
            if typ == "file":
                if not resume_path.is_file():
                    return self._skip(SkipReason.RESUME_UNAVAILABLE, "selected resume file is missing")
                field.set_input_files(str(resume_path))
                questions.append({"label": "resume upload", "kind": QuestionKind.SAFE_STANDARD.value, "required": required})
                continue
            kind, answer = self.classifier.classify(label, required)
            questions.append({"label": label[:180], "kind": kind.value, "required": required, "answer_key": answer.key if answer else ""})
            if kind == QuestionKind.SENSITIVE:
                return self._skip(SkipReason.SENSITIVE_INFORMATION_REQUESTED, f"sensitive required field: {label}", questions)
            if kind == QuestionKind.ASSESSMENT:
                return self._skip(SkipReason.ASSESSMENT_REQUIRED, f"assessment field: {label}", questions)
            if kind in {QuestionKind.UNKNOWN, QuestionKind.DISQUALIFYING} and required:
                return self._skip(SkipReason.UNKNOWN_ANSWER, f"required answer is not verified: {label}", questions)
            if kind == QuestionKind.SAFE_GENERATIVE:
                custom += 1
                if required:
                    value = self._truthful_short_answer(job)
                else:
                    continue
            elif answer and answer.status == "VERIFIED":
                value = answer.value
            else:
                continue
            self._fill(field, typ, value)

        complexity = complexity_decision(custom, self._page_count(page), self.settings, fields.count())
        if not complexity.allowed:
            return self._skip(complexity.reason, complexity.detail, questions)

        submit = page.get_by_role("button", name=re.compile(r"^(submit|submit application|apply)$", re.I))
        if submit.count() == 0:
            next_button = page.get_by_role("button", name=re.compile(r"^(next|continue|save and continue)$", re.I))
            if next_button.count():
                return self._skip(SkipReason.APPLICATION_TOO_LONG, "multi-page application is not quick apply", questions)
            return self._skip(SkipReason.VALIDATION_FAILED, "final submit action not found", questions)
        if not submit.first.is_enabled():
            return self._skip(SkipReason.VALIDATION_FAILED, "final submit action is disabled", questions)

        invalid = page.locator(":invalid:visible")
        if invalid.count():
            labels = [self._label(page, invalid.nth(i)) for i in range(min(invalid.count(), 5))]
            return self._skip(SkipReason.UNKNOWN_ANSWER, "unresolved required fields: " + ", ".join(labels), questions)
        if dry_run or not self.settings.auto_submit:
            return ApplicationResult(status="would_submit", questions=questions, ats=self.name, would_submit=True, detail="validation passed; final click suppressed")

        submit.first.click()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30_000)
        except Exception:
            # Many ATS forms confirm in-place without a navigation event.
            pass
        confirmation = page.locator("body").inner_text(timeout=10_000)
        matched = re.search(r"(application (has been )?(submitted|received)|thank you for applying|thanks for applying)", confirmation, re.I)
        if not matched:
            return ApplicationResult(status="failed", detail="submit clicked but confirmation could not be verified", questions=questions, ats=self.name)
        confirmation_text = matched.group(0)
        confirmation_id = self._confirmation_id(confirmation, page.url)
        return ApplicationResult(status="submitted", confirmation_id=confirmation_id, confirmation_text=confirmation_text, questions=questions, ats=self.name)

    def _label(self, page, field) -> str:
        return str(field.evaluate("""el => {
          const byFor = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
          const wrapped = el.closest('label');
          const group = el.closest('fieldset');
          return (byFor?.innerText || wrapped?.innerText || el.getAttribute('aria-label') ||
                  el.getAttribute('placeholder') || group?.querySelector('legend')?.innerText ||
                  el.name || el.id || 'unlabeled field').trim();
        }"""))

    def _fill(self, field, typ: str, value) -> None:
        text = "Yes" if value is True else "No" if value is False else str(value)
        tag = field.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            try:
                field.select_option(label=text)
            except Exception:
                field.select_option(value=text)
        elif typ in {"checkbox", "radio"}:
            if text.lower() in {"yes", "true", "1"}:
                field.check()
        else:
            field.fill(text)

    def _truthful_short_answer(self, job: Job) -> str:
        degree = self.profile.field("education.degree", "LSU student")
        return f"I am an LSU {degree} student with verified customer-service, construction, and automation project experience. This {job.title} role matches my goal of contributing reliably while building practical experience."

    def _page_count(self, page) -> int:
        text = page.locator("body").inner_text()
        match = re.search(r"step\s+\d+\s+(?:of|/)\s*(\d+)", text, re.I)
        return int(match.group(1)) if match else 1

    def _confirmation_id(self, text: str, url: str) -> str:
        match = re.search(r"(?:confirmation|application|reference)\s*(?:id|number|#)?\s*[:#]?\s*([A-Z0-9-]{5,})", text, re.I)
        return match.group(1) if match else url.rsplit("/", 1)[-1][:100]

    def _skip(self, reason, detail: str, questions: list[dict] | None = None) -> ApplicationResult:
        reason_value = reason.value if isinstance(reason, SkipReason) else str(reason)
        return ApplicationResult(status="skipped", skip_reason=reason_value, detail=detail[:300], questions=questions or [], ats=self.name)
