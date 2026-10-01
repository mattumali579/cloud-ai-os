"""Public-website enrichment: published emails + personalization facts.

Only pages the business itself publishes are read (homepage, contact, about),
robots.txt is honoured, and no address is ever guessed. Email statuses:

    published  seen on the company's own public page (or public listing tag)
    validated  published AND syntax ok AND the mail domain has MX records
               (the mail SERVER exists; the mailbox itself is not probed)
    invalid    malformed, or the domain accepts no mail
    probable / unknown  reserved; this module never produces guesses
"""
from __future__ import annotations

import html as htmllib
import json
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from cloudos.leadgen.normalize import (
    FREEMAIL,
    email_domain,
    is_shared_host,
    normalize_email,
    registrable_domain,
)

USER_AGENT = "Mozilla/5.0 (compatible; BrightReachLeadResearch/1.0; +https://github.com/mattumali579/cloud-ai-os)"

_EMAIL_IN_TEXT = re.compile(r"[a-z0-9._%+\-]{1,64}@[a-z0-9.\-]{1,190}\.[a-z]{2,24}", re.I)
_JUNK_LOCAL = re.compile(r"^(example|test|noreply|no-reply|donotreply|do-not-reply|user|name|email|your|yourname|john|jane|abuse|postmaster|webmaster|privacy|accessibility|wordpress|careers|career|jobs|job|hr|hiring|recruiting|resumes|employment|billing|accounting|payments|ap|invoices)$")
_JUNK_DOMAIN = re.compile(r"(sentry|wixpress|example\.|domain\.com|email\.com|yoursite|yourdomain|godaddy|squarespace|wix\.com|mysite|sentry\.io|\.png|\.jpg|\.jpeg|\.gif|\.webp|\.svg)")
_CONTACT_LINK = re.compile(r"contact|about|our-team|staff|our-story|get-in-touch|reach-us|location", re.I)
_SKIP_LINK = re.compile(r"career|job|join|employ|hiring|apply|resume|recruit|privacy|terms|login|cart", re.I)
_PREFERRED_LOCAL = ["info", "office", "contact", "hello", "sales", "service", "support", "admin", "team", "book", "appointments", "frontdesk", "help"]
_PARKED = re.compile(r"domain (is )?for sale|buy this domain|this domain may be for sale|parked free|domain parking|hugedomains|sedo\.com|dan\.com|website is under construction|account suspended|this site can.t be reached", re.I)
_SINCE = re.compile(r"\b(?:since|established|est\.?|founded(?: in)?|serving [a-z ,]+ since)\s*(19[5-9]\d|20[0-2]\d)\b", re.I)
_FAMILY = re.compile(r"family[- ]owned|locally owned|owner[- ]operated|veteran[- ]owned|woman[- ]owned|family business", re.I)
_EMERGENCY = re.compile(r"24/7|24 hours|emergency service|same[- ]day", re.I)
_FINANCING = re.compile(r"financing available|0% financing|financing options", re.I)
_FREE_EST = re.compile(r"free (estimate|quote|inspection|consultation|trial|class)", re.I)
_BOOKING = re.compile(r"book (now|online|an appointment)|schedule (online|now|service|an appointment)|request (a )?(quote|estimate|appointment)", re.I)
_CHAT = re.compile(r"livechat|tawk\.to|intercom|drift\.com|podium|birdeye|hatchapp|smith\.ai|callrail", re.I)


@dataclass
class EmailFinding:
    email: str
    status: str
    source_url: str
    role: str = "generic"


@dataclass
class Enrichment:
    ok: bool
    website: str
    final_url: str = ""
    dead: bool = False
    parked: bool = False
    blocked: bool = False   # site is alive but refuses automated reading (bot wall, 401/403/429)
    error: str = ""
    emails: list[EmailFinding] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    pages_read: int = 0

    @property
    def best_email(self) -> EmailFinding | None:
        return self.emails[0] if self.emails else None


@lru_cache(maxsize=4096)
def mx_ok(domain: str) -> bool:
    if not domain:
        return False
    try:
        import dns.resolver

        answers = dns.resolver.resolve(domain, "MX", lifetime=6)
        return any(str(r.exchange).strip(".") for r in answers)
    except Exception:  # noqa: BLE001 — NXDOMAIN, NoAnswer, timeout all mean "not validated"
        return False


def _decode_cfemail(hexstr: str) -> str:
    try:
        data = bytes.fromhex(hexstr)
        key = data[0]
        return bytes(b ^ key for b in data[1:]).decode("utf-8", "ignore")
    except ValueError:
        return ""


def extract_emails(page_html: str) -> list[str]:
    """All plausible addresses on one page, in document order, de-obfuscated."""
    text = htmllib.unescape(page_html or "")
    found: list[str] = []
    found += re.findall(r"mailto:([^\"'?>\s]+)", text, re.I)
    found += [_decode_cfemail(h) for h in re.findall(r"data-cfemail=[\"']([0-9a-f]+)[\"']", text, re.I)]
    for block in re.findall(r"<script[^>]+ld\+json[^>]*>(.*?)</script>", text, re.I | re.S):
        found += re.findall(r"\"email\"\s*:\s*\"([^\"]+)\"", block)
    visible = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.I | re.S)
    visible = re.sub(r"\s*(\[at\]|\(at\))\s*", "@", visible, flags=re.I)
    visible = re.sub(r"\s*(\[dot\]|\(dot\))\s*", ".", visible, flags=re.I)
    found += _EMAIL_IN_TEXT.findall(re.sub(r"<[^>]+>", " ", visible))
    out: list[str] = []
    for raw in found:
        email = normalize_email(raw)
        if not email or email in out:
            continue
        local, _, dom = email.partition("@")
        if _JUNK_LOCAL.match(local) or _JUNK_DOMAIN.search(dom) or re.search(r"\d{5,}", local):
            continue
        out.append(email)
    return out


def rank_emails(emails: list[str], site_domain: str) -> list[tuple[str, str]]:
    """Keep own-domain + freemail addresses; order generic business inboxes first.

    Addresses on a third-party domain (the web designer, a franchisor) are
    dropped: they are not this company's inbox.
    """
    own = registrable_domain(site_domain)
    ranked: list[tuple[int, str, str]] = []
    for email in emails:
        dom = email_domain(email)
        is_own = own and registrable_domain(dom) == own
        if not is_own and dom not in FREEMAIL:
            continue
        local = email.partition("@")[0]
        if local in _PREFERRED_LOCAL:
            score, role = _PREFERRED_LOCAL.index(local), "generic"
        elif re.match(r"^[a-z]+(\.[a-z]+)?$", local) and is_own:
            score, role = 30, "person"
        else:
            score, role = 40, "generic"
        if not is_own:
            score += 50  # freemail: acceptable when published, but own domain wins
        ranked.append((score, email, role))
    ranked.sort()
    return [(e, r) for _, e, r in ranked]


def _visible_text(page_html: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", page_html or "", flags=re.I | re.S)
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def extract_facts(page_html: str, text: str) -> dict:
    facts: dict = {}
    title = re.search(r"<title[^>]*>(.*?)</title>", page_html or "", re.I | re.S)
    if title:
        facts["site_title"] = re.sub(r"\s+", " ", htmllib.unescape(title.group(1))).strip()[:140]
    desc = re.search(r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']+)", page_html or "", re.I)
    if desc:
        facts["site_description"] = htmllib.unescape(desc.group(1)).strip()[:220]
    since = _SINCE.search(text)
    if since:
        facts["since_year"] = int(since.group(1))
    for key, rx in (("ownership", _FAMILY), ("emergency_service", _EMERGENCY), ("financing", _FINANCING),
                    ("free_estimate_offer", _FREE_EST), ("online_booking", _BOOKING)):
        m = rx.search(text)
        if m:
            facts[key] = m.group(0).lower()
    facts["has_chat_or_call_tracking"] = bool(_CHAT.search(page_html or ""))
    facts["has_contact_form"] = bool(re.search(r"<form[^>]*>.*?(email|phone|message)", page_html or "", re.I | re.S))
    return facts


class Fetcher:
    """Polite HTTP: robots.txt honoured, short timeouts, capped pages per site."""

    def __init__(self, timeout: float = 12.0, max_pages: int = 4):
        self.max_pages = max_pages
        self.client = httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"},
            verify=False,  # many small-business certs are broken; we only read public HTML
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self.last_status: dict[str, int | str] = {}

    def close(self) -> None:
        self.client.close()

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            rp = RobotFileParser()
            try:
                resp = self.client.get(origin + "/robots.txt", timeout=6)
                if resp.status_code == 200 and "text" in resp.headers.get("content-type", "text"):
                    rp.parse(resp.text.splitlines())
                    self._robots[origin] = rp
                else:
                    self._robots[origin] = None
            except httpx.HTTPError:
                self._robots[origin] = None
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    def get(self, url: str) -> httpx.Response | None:
        if not self.allowed(url):
            self.last_status[url] = "robots"
            return None
        resp = self.client.get(url)
        self.last_status[url] = resp.status_code
        ctype = resp.headers.get("content-type", "")
        if resp.status_code >= 400 or ("html" not in ctype and "text" not in ctype and ctype):
            return None
        return resp


def enrich_website(website: str, fetcher: Fetcher, extra_emails: list[tuple[str, str]] | None = None) -> Enrichment:
    """Read up to ``max_pages`` public pages of one site; return emails + facts."""
    url = website if "://" in website else "http://" + website
    result = Enrichment(ok=False, website=website)
    try:
        home = fetcher.get(url)
    except httpx.HTTPError as exc:
        result.dead, result.error = True, f"unreachable: {type(exc).__name__}"
        return result
    if home is None:
        status = fetcher.last_status.get(url)
        if status in (401, 403, 429, 503) or status == "robots":
            result.blocked, result.error = True, f"site refuses automated reading ({status})"
            # a published listing email still counts; nothing is guessed
            for email, src in extra_emails or []:
                email = normalize_email(email)
                if email:
                    st = "validated" if mx_ok(email_domain(email)) else "invalid"
                    result.emails.append(EmailFinding(email=email, status=st, source_url=src))
            result.ok = bool(result.emails)
            return result
        result.dead, result.error = True, f"homepage not readable (status {status})"
        return result
    result.final_url = str(home.url)
    pages = [(str(home.url), home.text)]
    text = _visible_text(home.text)
    if _PARKED.search(text[:4000]) or len(text) < 80:
        result.parked, result.error = True, "parked or empty site"
        return result

    if not is_shared_host(result.final_url):
        base_host = urlparse(result.final_url).hostname or ""
        links = []
        for href in re.findall(r"href=[\"']([^\"'#]+)", home.text, re.I):
            absolute = urljoin(result.final_url, href)
            host = urlparse(absolute).hostname or ""
            if registrable_domain(host) == registrable_domain(base_host) and _CONTACT_LINK.search(absolute) and not _SKIP_LINK.search(absolute) and absolute not in links:
                links.append(absolute)
        links.sort(key=lambda u: (0 if "contact" in u.lower() else 1, len(u)))
        for link in links[: max(0, fetcher.max_pages - 1)]:
            try:
                resp = fetcher.get(link)
            except httpx.HTTPError:
                continue
            if resp is not None:
                pages.append((str(resp.url), resp.text))
                time.sleep(0.3)

    result.pages_read = len(pages)
    site_domain = urlparse(result.final_url).hostname or ""
    seen: dict[str, str] = {}
    for page_url, page_html in pages:
        for email in extract_emails(page_html):
            seen.setdefault(email, page_url)
    for email, src in extra_emails or []:
        email = normalize_email(email)
        if email:
            seen.setdefault(email, src)
    for email, role in rank_emails(list(seen), site_domain):
        status = "validated" if mx_ok(email_domain(email)) else "invalid"
        result.emails.append(EmailFinding(email=email, status=status, source_url=seen[email], role=role))
    result.emails.sort(key=lambda f: 0 if f.status == "validated" else 1)

    all_text = " ".join(_visible_text(h) for _, h in pages)
    result.facts = extract_facts(home.text, all_text)
    result.facts["pages_read"] = len(pages)
    result.ok = True
    return result


def facts_json(facts: dict) -> str:
    return json.dumps(facts, sort_keys=True, default=str)
