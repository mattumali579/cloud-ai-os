"""Discovery sources. Each one is independent: a failure in one never stops the others.

    osm               OpenStreetMap Overpass API (free, open data, ODbL)
    google_maps       self-hosted gosom/google-maps-scraper container (already used by
                      the leadgen-pipeline GitHub workflows); no paid API, no Apify
    history_recovery  old ledger companies that were never contacted because no
                      email was found at the time - re-checked, never re-contacted
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from typing import Iterable

import httpx

from cloudos.leadgen.normalize import city_state_from_address, normalize_state
from cloudos.leadgen.store import Candidate

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = {"User-Agent": "BrightReachLeadEngine/1.0 (+https://github.com/mattumali579/cloud-ai-os)"}


class SourceError(RuntimeError):
    """The source itself failed (network, service down) - counts against its health."""


class Source:
    name = "base"

    def queries(self, cfg: dict) -> list[dict]:
        raise NotImplementedError

    def search(self, q: dict, cfg: dict) -> list[Candidate]:
        raise NotImplementedError


# --------------------------------------------------------------------------- OSM

_FILTER = re.compile(r'\["([^"]+)"(=|~)"([^"]+)"\]')


def _matches(tags: dict, flt: str) -> bool:
    for key, op, val in _FILTER.findall(flt):
        have = tags.get(key)
        if have is None:
            return False
        if op == "=" and have != val:
            return False
        if op == "~" and not re.search(val, have, re.I):
            return False
    return True


class OSMSource(Source):
    name = "osm"

    def __init__(self) -> None:
        self._last_call = 0.0

    def queries(self, cfg: dict) -> list[dict]:
        return [{"query": f"osm all-industries around {loc['city']}, {loc['state']}", "location": f"{loc['city']}, {loc['state']}",
                 "industry": "*", "loc": loc} for loc in cfg["locations"]]

    def _overpass(self, ql: str, min_interval: float, deadline: float | None = None) -> dict:
        """Fetch one Overpass result without running past the enclosing refill.

        A failed endpoint used to spend up to 120 seconds per retry plus an
        exponential sleep.  Across the fallback endpoints that could outlive
        the engine's configured ``max_minutes`` and leave its worker occupied.
        ``deadline`` makes each request and backoff consume only the time the
        caller actually budgeted for discovery.
        """
        wait = min_interval - (time.time() - self._last_call)
        if wait > 0:
            if deadline is not None:
                wait = min(wait, max(0.0, deadline - time.time()))
            if wait:
                time.sleep(wait)
        errors = []
        for attempt, endpoint in enumerate(OVERPASS_ENDPOINTS * 2):
            remaining = (deadline - time.time()) if deadline is not None else 120.0
            if remaining <= 0:
                raise SourceError("overpass skipped: discovery time budget exhausted")
            try:
                self._last_call = time.time()
                resp = httpx.post(endpoint, data={"data": ql}, headers=UA, timeout=max(1.0, min(120.0, remaining)))
                if resp.status_code in (429, 502, 503, 504):
                    errors.append(f"{endpoint.split('/')[2]} {resp.status_code}")
                    backoff = min(60, 5 * 2 ** attempt)
                    if deadline is not None:
                        backoff = min(backoff, max(0.0, deadline - time.time()))
                    if backoff:
                        time.sleep(backoff)
                    continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"{endpoint.split('/')[2]} {type(exc).__name__}")
                backoff = min(60, 5 * 2 ** attempt)
                if deadline is not None:
                    backoff = min(backoff, max(0.0, deadline - time.time()))
                if backoff:
                    time.sleep(backoff)
        raise SourceError("overpass unavailable: " + "; ".join(errors[-4:]))

    def search(self, q: dict, cfg: dict) -> list[Candidate]:
        loc = q["loc"]
        scfg = cfg["sources"]["osm"]
        radius = int(scfg.get("radius_m", 25000))
        parts = []
        for ind in cfg["industries"].values():
            for flt in ind["osm"]:
                for wkey in ("website", "contact:website", "url"):
                    parts.append(f'nwr(around:{radius},{loc["lat"]},{loc["lon"]}){flt}["{wkey}"];')
        ql = f"[out:json][timeout:90];({''.join(parts)});out tags center;"
        data = self._overpass(ql, float(scfg.get("min_interval_s", 6)), cfg.get("_discovery_deadline"))
        out: list[Candidate] = []
        for el in data.get("elements", []):
            tags = el.get("tags") or {}
            name = tags.get("name") or tags.get("brand") or ""
            website = tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
            if not name or not website:
                continue
            industry = next((key for key, ind in cfg["industries"].items() if any(_matches(tags, f) for f in ind["osm"])), "")
            emails = [e.strip() for e in re.split(r"[;,]", tags.get("email") or tags.get("contact:email") or "") if e.strip()]
            out.append(Candidate(
                name=name, website=website, industry=industry,
                city=tags.get("addr:city") or loc["city"], state=normalize_state(tags.get("addr:state") or loc["state"]),
                phone=tags.get("phone") or tags.get("contact:phone") or "", source=self.name, query=q["query"],
                listing_emails=emails,
                # a Wikidata-linked brand is a national/regional chain, not an owner-run shop
                brand_tag=(tags.get("brand") or name) if (tags.get("brand:wikidata") or tags.get("operator:wikidata")) else "",
                extra={"osm_id": f"{el.get('type')}/{el.get('id')}"},
            ))
        return out


# -------------------------------------------------------------------- Google Maps

class GoogleMapsSource(Source):
    """Drives the self-hosted gosom/google-maps-scraper web API in small jobs."""

    name = "google_maps"

    def queries(self, cfg: dict) -> list[dict]:
        out = []
        for loc in cfg["locations"]:
            for key, ind in cfg["industries"].items():
                out.append({"query": f"{ind['maps']} in {loc['city']}, {loc['state']}", "industry": key,
                            "location": f"{loc['city']}, {loc['state']}", "loc": loc})
        return out

    def _base(self, cfg: dict) -> str:
        base = os.environ.get(cfg["sources"]["google_maps"].get("url_env", "GMAPS_SCRAPER_URL"), "").rstrip("/")
        if not base:
            raise SourceError("GMAPS_SCRAPER_URL not set - scraper container not running here")
        return base

    def search(self, q: dict, cfg: dict) -> list[Candidate]:
        scfg = cfg["sources"]["google_maps"]
        base = self._base(cfg)
        max_time = int(scfg.get("max_time_s", 420))
        try:
            job = httpx.post(f"{base}/api/v1/jobs", timeout=30, json={
                "name": f"lead-engine-{int(time.time())}", "keywords": [q["query"]], "lang": "en", "zoom": 15,
                "lat": "0", "lon": "0", "fast_mode": False, "radius": 10000, "depth": int(scfg.get("depth", 1)),
                "email": False, "max_time": max_time, "proxies": []}).json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"scraper unreachable: {type(exc).__name__}") from exc
        job_id = job.get("id") or job.get("ID")
        if not job_id:
            raise SourceError(f"scraper returned no job id: {str(job)[:120]}")
        status = ""
        deadline = time.time() + max_time + 180
        while time.time() < deadline:
            time.sleep(10)
            try:
                status = str(httpx.get(f"{base}/api/v1/jobs/{job_id}", timeout=20).json().get("Status", "")).lower()
            except (httpx.HTTPError, ValueError):
                continue
            if status in ("ok", "failed"):
                break
        try:
            resp = httpx.get(f"{base}/api/v1/jobs/{job_id}/download", timeout=60)
        except httpx.HTTPError as exc:
            raise SourceError(f"download failed ({status or 'no status'}): {type(exc).__name__}") from exc
        finally:
            try:
                httpx.delete(f"{base}/api/v1/jobs/{job_id}", timeout=10)
            except httpx.HTTPError:
                pass
        if resp.status_code >= 400:
            raise SourceError(f"no results file (job status {status or 'unknown'})")
        rows = list(self._rows_to_candidates(resp.text, q))
        if not rows:
            # Maps always has listings for "<trade> in <metro>"; empty usually means we were blocked.
            raise SourceError(f"0 listings returned (job status {status or 'unknown'}) - possibly blocked")
        return rows

    def _rows_to_candidates(self, text: str, q: dict) -> Iterable[Candidate]:
        for row in csv.DictReader(io.StringIO(text)):
            name = (row.get("title") or "").strip()
            if not name:
                continue
            city, state = "", ""
            try:
                addr = json.loads(row.get("complete_address") or "{}")
                city, state = addr.get("city") or "", normalize_state(addr.get("state") or "")
            except ValueError:
                pass
            if not city:
                city, state = city_state_from_address(row.get("address") or "")
            try:
                reviews = int(float(row.get("review_count") or 0))
            except ValueError:
                reviews = None
            try:
                rating = float(row.get("review_rating") or 0) or None
            except ValueError:
                rating = None
            yield Candidate(
                name=name, website=(row.get("website") or "").strip(), industry=q["industry"],
                city=city or q["loc"]["city"], state=state or q["loc"]["state"], phone=row.get("phone") or "",
                source=self.name, query=q["query"], review_count=reviews, rating=rating,
                extra={"maps_category": row.get("category") or "", "place_id": row.get("place_id") or "",
                       "status": row.get("status") or ""},
            )


# ------------------------------------------------------------ history recovery

class HistoryRecoverySource(Source):
    """Old ledger rows marked 'No Email Found' etc. Never contacted -> re-check once."""

    name = "history_recovery"

    def queries(self, cfg: dict) -> list[dict]:
        return [{"query": "recheck uncontacted historical companies", "industry": "*", "location": "*"}]

    def search(self, q: dict, cfg: dict) -> list[Candidate]:  # engine feeds these from the DB
        return []


ALL_SOURCES = {cls.name: cls for cls in (OSMSource, GoogleMapsSource, HistoryRecoverySource)}
