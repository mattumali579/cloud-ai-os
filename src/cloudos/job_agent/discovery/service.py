from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
import yaml

from cloudos.config import Settings

from ..models import Job

log = logging.getLogger("cloudos.job_agent.discovery")


@dataclass
class DiscoveryResult:
    jobs: list[Job] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


class DiscoveryService:
    """Direct-employer feed discovery with a hard per-source failure boundary."""

    def __init__(self, settings: Settings, config_path: str | Path | None = None, client: httpx.Client | None = None):
        self.settings = settings
        self.config_path = Path(config_path or settings.job_agent_config_path)
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        self.client = client or httpx.Client(
            timeout=settings.discovery_http_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "CloudAIOS-JobAgent/1.0 (+direct employer job discovery)"},
        )

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult()
        for source in self.config.get("sources", []):
            if not source.get("enabled", True):
                continue
            try:
                adapter = getattr(self, f"_{source.get('type')}")
                result.jobs.extend(adapter(source))
            except Exception as exc:  # one dead feed never stops discovery
                name = str(source.get("name") or source.get("url") or "unknown")
                result.errors.append({"source": name, "error": f"{type(exc).__name__}: {exc}"[:300]})
                log.warning("job source %s failed: %s", name, exc)
        return result

    def _greenhouse(self, source: dict[str, Any]) -> list[Job]:
        response = self.client.get(source["url"], params={"content": "true"})
        response.raise_for_status()
        jobs = []
        for item in response.json().get("jobs", []):
            jobs.append(Job(
                company=source.get("employer") or source["name"],
                title=str(item.get("title") or ""),
                location=str((item.get("location") or {}).get("name") or ""),
                job_url=str(item.get("absolute_url") or ""),
                application_url=str(item.get("absolute_url") or ""),
                ats="greenhouse",
                ats_job_id=str(item.get("id") or ""),
                requisition_id=str(item.get("internal_job_id") or ""),
                description=_clean_html(str(item.get("content") or "")),
                source=source["name"],
                metadata={"updated_at": item.get("updated_at")},
            ))
        return jobs

    def _lever(self, source: dict[str, Any]) -> list[Job]:
        response = self.client.get(source["url"])
        response.raise_for_status()
        jobs = []
        for item in response.json():
            categories = item.get("categories") or {}
            description = "\n".join(str(v) for v in (item.get("descriptionPlain"), item.get("additionalPlain")) if v)
            jobs.append(Job(
                company=source.get("employer") or source["name"],
                title=str(item.get("text") or ""),
                location=str(categories.get("location") or ""),
                job_url=str(item.get("hostedUrl") or ""),
                application_url=str(item.get("applyUrl") or item.get("hostedUrl") or ""),
                ats="lever",
                ats_job_id=str(item.get("id") or ""),
                description=description,
                source=source["name"],
            ))
        return jobs

    def _workday(self, source: dict[str, Any]) -> list[Job]:
        jobs = []
        careers = str(source.get("careers_url") or "").rstrip("/")
        for offset in range(0, int(source.get("max", 400)), 20):
            response = self.client.post(source["url"], json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": source.get("search_text", "")})
            response.raise_for_status()
            body = response.json()
            postings = body.get("jobPostings", [])
            for item in postings:
                external = str(item.get("externalPath") or "")
                jobs.append(Job(
                    company=source.get("employer") or source["name"],
                    title=str(item.get("title") or ""),
                    location=f"{item.get('locationsText') or 'LSU'}, Louisiana",
                    job_url=f"{careers}{external}",
                    application_url=f"{careers}{external}",
                    ats="workday",
                    ats_job_id=external.rsplit("/", 1)[-1],
                    description=str(item.get("bulletFields") or ""),
                    source=source["name"],
                    metadata={"posted_on": item.get("postedOn")},
                ))
            if not postings or offset + 20 >= int(body.get("total") or 0):
                break
        return jobs

    def _governmentjobs(self, source: dict[str, Any]) -> list[Job]:
        """Render the official government ATS listing; its jobs are client-rendered."""
        from playwright.sync_api import sync_playwright

        jobs: dict[str, Job] = {}
        executable = self.settings.job_browser_executable_path or None
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=executable, args=["--disable-dev-shm-usage", "--no-sandbox"])
            try:
                page = browser.new_page()
                page.set_default_timeout(30_000)
                for keyword in source.get("keywords", [""]):
                    page.goto(f"{source['url']}?keywords={quote_plus(str(keyword))}", wait_until="networkidle", timeout=60_000)
                    anchors = page.locator("a")
                    for index in range(anchors.count()):
                        anchor = anchors.nth(index)
                        href = anchor.get_attribute("href") or ""
                        if "/careers/louisiana/jobs/" not in href:
                            continue
                        url = href if href.startswith("http") else "https://www.governmentjobs.com" + href
                        path = href.split("?", 1)[0]
                        job_id = path.split("/jobs/", 1)[-1].split("/", 1)[0]
                        if job_id in jobs:
                            continue
                        title = anchor.inner_text().strip()
                        card = anchor.evaluate("el => el.parentElement.parentElement.innerText") or ""
                        location_match = re.search(r"([A-Za-z .'-]+,\s*LA)\b", card)
                        jobs[job_id] = Job(
                            company=source.get("employer") or source["name"],
                            title=title,
                            location=location_match.group(1).strip() if location_match else "Louisiana",
                            job_url=url,
                            application_url=url,
                            ats="governmentjobs",
                            ats_job_id=job_id,
                            description=re.sub(r"\s+", " ", card).strip(),
                            source=source["name"],
                        )
            finally:
                browser.close()
        return list(jobs.values())


def _clean_html(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()
