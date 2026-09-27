from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from ..models import Job
from ..second_brain.experience_reader import ExperienceReader
from ..second_brain.profile_reader import ApplicantProfile
from ..second_brain.project_reader import ProjectReader


VARIANTS = {
    "desk-office": "desk-office-resume.pdf",
    "construction-management": "construction-management-resume.pdf",
    "ai-automation": "ai-automation-resume.pdf",
    "general-student": "general-student-resume.pdf",
}


def select_resume_variant(category: str) -> str:
    return {
        "desk-office": "desk-office",
        "student-worker": "general-student",
        "gym-front-desk": "general-student",
        "ai-automation": "ai-automation",
        "construction-management": "construction-management",
        "owner-apprentice": "desk-office",
    }.get(category, "general-student")


class ResumeGenerator:
    def __init__(self, profile: ApplicantProfile, output_dir: str | Path):
        self.profile = profile
        self.output_dir = Path(output_dir)

    def generate_all(self) -> dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return {name: self.generate(name) for name in VARIANTS}

    def generate(self, variant: str, job: Job | None = None) -> Path:
        if variant not in VARIANTS:
            raise ValueError(f"unknown resume variant: {variant}")
        if job:
            target = self.output_dir / "tailored" / f"{job.job_id}-{variant}.pdf"
        else:
            target = self.output_dir / VARIANTS[variant]
        target.parent.mkdir(parents=True, exist_ok=True)
        self._render(target, variant, job)
        return target

    def _render(self, target: Path, variant: str, job: Job | None) -> None:
        styles = getSampleStyleSheet()
        name_style = ParagraphStyle("Name", parent=styles["Title"], alignment=TA_CENTER, fontSize=17, leading=19, spaceAfter=4)
        contact_style = ParagraphStyle("Contact", parent=styles["Normal"], alignment=TA_CENTER, fontSize=8.5, leading=10)
        heading = ParagraphStyle("Heading", parent=styles["Heading2"], fontSize=10.5, leading=12, spaceBefore=7, spaceAfter=3)
        body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9, leading=11, spaceAfter=2)
        doc = SimpleDocTemplate(str(target), pagesize=letter, leftMargin=.55*inch, rightMargin=.55*inch, topMargin=.45*inch, bottomMargin=.45*inch, title=f"Resume - {variant}")
        story = []
        name = self.profile.field("identity.name", "")
        contact = " | ".join(filter(None, [self.profile.field("contact.city"), self.profile.field("contact.state"), self.profile.field("contact.phone"), self.profile.field("contact.email"), self.profile.field("contact.linkedin")]))
        story.extend([Paragraph(_esc(name), name_style), Paragraph(_esc(contact), contact_style), Spacer(1, 5)])
        story.extend([Paragraph("PROFESSIONAL SUMMARY", heading), Paragraph(_esc(self._summary(variant)), body)])
        skills = self._skills(variant, job)
        story.extend([Paragraph("SKILLS", heading), Paragraph(_esc(" | ".join(skills)), body)])
        if variant == "ai-automation":
            story.append(Paragraph("PROJECTS", heading))
            for project in ProjectReader(self.profile).verified():
                story.append(Paragraph(f"<b>{_esc(project.get('name', 'Personal project'))}</b> - {_esc(project.get('role', ''))}", body))
                for bullet in project.get("bullets", []):
                    story.append(Paragraph(f"&#8226; {_esc(bullet)}", body))
        story.append(Paragraph("EXPERIENCE", heading))
        experiences = ExperienceReader(self.profile).verified()
        if variant == "construction-management":
            experiences.sort(key=lambda x: 0 if "Independent" in x.get("employer", "") or "Fencing" in x.get("employer", "") else 1)
        for exp in experiences:
            story.append(Paragraph(f"<b>{_esc(exp.get('title', ''))}</b> - {_esc(exp.get('employer', ''))} | {_esc(exp.get('dates', ''))}", body))
            for bullet in exp.get("bullets", []):
                story.append(Paragraph(f"&#8226; {_esc(bullet)}", body))
        story.append(Paragraph("EDUCATION", heading))
        story.append(Paragraph(f"<b>{_esc(self.profile.field('education.school', ''))}</b> - {_esc(self.profile.field('education.degree', ''))}, Expected {_esc(self.profile.field('education.graduation', ''))}", body))
        leadership = self.profile.field("leadership.wrestling_captain")
        if leadership:
            story.extend([Paragraph("LEADERSHIP", heading), Paragraph(_esc(leadership), body)])
        doc.build(story)

    def _summary(self, variant: str) -> str:
        summaries = {
            "desk-office": "LSU Construction Management student with customer-facing retail experience, Microsoft Office skills, and experience independently managing automated workflows. Reliable, organized, and ready to support office operations.",
            "construction-management": "LSU Construction Management student with hands-on site preparation, material measurement, fencing installation, solar support construction, equipment operation, documentation, and general safety awareness.",
            "ai-automation": "LSU student who builds personal AI and automation projects using Python, Node.js, Playwright, APIs, and multi-step data workflows. Projects are independent work, not professional AI employment.",
            "general-student": "LSU student and five-year wrestling team captain with customer service, construction, and equipment experience. Dependable, disciplined, quick to learn, and available around classes and during summers.",
        }
        return summaries[variant]

    def _skills(self, variant: str, job: Job | None) -> list[str]:
        keys = {
            "desk-office": ("office", "automation"),
            "construction-management": ("construction", "office"),
            "ai-automation": ("automation", "office"),
            "general-student": ("office", "construction"),
        }[variant]
        values: list[str] = []
        for key in keys:
            values.extend(self.profile.field(f"skills.{key}", []) or [])
        if job:
            terms = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z+#.-]{2,}", job.description)}
            values.sort(key=lambda value: (not any(word.lower() in terms for word in re.findall(r"[A-Za-z]+", value)), value.lower()))
        return list(dict.fromkeys(values))[:18]


def _esc(value: object) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
