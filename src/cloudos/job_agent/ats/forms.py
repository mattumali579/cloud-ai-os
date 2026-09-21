from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

from ..answers import QuestionClassifier
from ..models import QuestionKind, SkipReason
from ..policy import inspect_page_text


@dataclass
class FormField:
    label: str
    name: str = ""
    input_type: str = "text"
    required: bool = False
    options: list[str] = field(default_factory=list)


@dataclass
class FormInspection:
    fields: list[FormField]
    questions: list[dict]
    allowed: bool
    skip_reason: str = ""
    custom_question_count: int = 0


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.fields: list[FormField] = []
        self.labels: dict[str, str] = {}
        self._label_for = ""
        self._label_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "label":
            self._label_for = values.get("for") or ""
            self._label_text = []
        if tag in {"input", "textarea", "select"}:
            typ = (values.get("type") or ("textarea" if tag == "textarea" else "select" if tag == "select" else "text")).lower()
            if typ in {"hidden", "submit", "button", "reset"}:
                return
            name = values.get("name") or values.get("id") or ""
            label = values.get("aria-label") or values.get("placeholder") or name
            required = "required" in values or values.get("aria-required") == "true"
            self.fields.append(FormField(label=label, name=name, input_type=typ, required=required))

    def handle_endtag(self, tag: str) -> None:
        if tag == "label" and self._label_for:
            self.labels[self._label_for] = " ".join(self._label_text).strip()
            self._label_for = ""
            self._label_text = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.text.append(value)
            if self._label_for:
                self._label_text.append(value)

    def finalize(self) -> None:
        for item in self.fields:
            if item.name in self.labels:
                item.label = self.labels[item.name]


def inspect_html(html: str, classifier: QuestionClassifier, settings) -> FormInspection:
    parser = _Parser()
    parser.feed(html)
    parser.finalize()
    page_decision = inspect_page_text(" ".join(parser.text), settings)
    if not page_decision.allowed:
        return FormInspection(parser.fields, [], False, page_decision.reason)
    questions = []
    custom = 0
    for item in parser.fields:
        if item.input_type == "file":
            questions.append({"label": item.label, "kind": QuestionKind.SAFE_STANDARD.value, "required": item.required, "answer_key": "resume"})
            continue
        kind, answer = classifier.classify(item.label, item.required)
        questions.append({"label": item.label, "kind": kind.value, "required": item.required, "answer_key": answer.key if answer else ""})
        if item.input_type != "file" and kind not in {QuestionKind.SAFE_STANDARD}:
            custom += 1
        if kind == QuestionKind.SENSITIVE:
            return FormInspection(parser.fields, questions, False, SkipReason.SENSITIVE_INFORMATION_REQUESTED.value, custom)
        if kind == QuestionKind.ASSESSMENT:
            return FormInspection(parser.fields, questions, False, SkipReason.ASSESSMENT_REQUIRED.value, custom)
        if kind == QuestionKind.UNKNOWN and item.required:
            return FormInspection(parser.fields, questions, False, SkipReason.UNKNOWN_ANSWER.value, custom)
        if kind == QuestionKind.DISQUALIFYING and item.required and (not answer or answer.status != "VERIFIED"):
            return FormInspection(parser.fields, questions, False, SkipReason.UNKNOWN_ANSWER.value, custom)
    if custom > settings.max_custom_questions:
        return FormInspection(parser.fields, questions, False, SkipReason.APPLICATION_TOO_LONG.value, custom)
    return FormInspection(parser.fields, questions, True, custom_question_count=custom)
