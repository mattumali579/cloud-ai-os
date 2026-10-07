"""Backfill public Google Maps evidence for existing campaign leads.

Uses the already-running gosom/google-maps-scraper, matches results to the
canonical company ids exported from Supabase, and writes a JSON update batch.
It never sends outreach and never creates a second lead store.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import time
from collections import Counter
from pathlib import Path

import httpx

from cloudos.leadgen.normalize import normalize_domain, normalize_name

MAPS_TERM = {
    "roofing": "roofing contractor", "hvac": "hvac contractor", "plumbing": "plumber",
    "electrical": "electrician", "med_spa": "med spa", "dentist": "dentist",
}


def _json(value, default):
    try:
        parsed = json.loads(value or "")
        return parsed if isinstance(parsed, type(default)) else default
    except (TypeError, ValueError):
        return default


def _review_signals(raw: str) -> dict:
    reviews = _json(raw, [])
    negatives, unanswered, samples = 0, 0, []
    for item in reviews[:12]:
        rating = item.get("Rating") or item.get("rating")
        try:
            rating = float(rating)
        except (TypeError, ValueError):
            rating = None
        response = (item.get("OwnerResponse") or item.get("owner_response") or item.get("Response") or "")
        when = item.get("When") or item.get("Date") or item.get("PublishedAtDate") or item.get("published_at") or ""
        text = item.get("Description") or item.get("Text") or item.get("text") or ""
        if rating is not None and rating <= 3:
            negatives += 1
            unanswered += int(not bool(response))
            if len(samples) < 3:
                samples.append({"rating": rating, "date": str(when)[:80], "owner_response": bool(response),
                                "text": " ".join(str(text).split())[:240]})
    return {"recent_negative_reviews": negatives, "recent_unanswered_negative_reviews": unanswered,
            "negative_review_samples": samples}


def scrape(base: str, query: str, timeout: int = 180) -> list[dict]:
    payload = {"name": f"review-campaign-{int(time.time())}", "keywords": [query], "lang": "en", "zoom": 15,
               "lat": "0", "lon": "0", "fast_mode": False, "radius": 10000, "depth": 1,
               "email": False, "max_time": timeout, "proxies": []}
    job = httpx.post(f"{base}/api/v1/jobs", json=payload, timeout=30).json()
    job_id = job.get("id") or job.get("ID")
    if not job_id:
        return []
    try:
        deadline = time.time() + timeout + 90
        status = ""
        while time.time() < deadline:
            time.sleep(5)
            try:
                status = str(httpx.get(f"{base}/api/v1/jobs/{job_id}", timeout=20).json().get("Status", "")).lower()
            except (httpx.HTTPError, ValueError):
                continue
            if status in ("ok", "failed"):
                break
        response = httpx.get(f"{base}/api/v1/jobs/{job_id}/download", timeout=60)
        return list(csv.DictReader(io.StringIO(response.text))) if response.status_code < 400 else []
    finally:
        try:
            httpx.delete(f"{base}/api/v1/jobs/{job_id}", timeout=10)
        except httpx.HTTPError:
            pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--url", default="http://127.0.0.1:8090")
    p.add_argument("--groups", type=int, default=30)
    args = p.parse_args()
    leads = json.loads(Path(args.input).read_text(encoding="utf-8"))
    groups = Counter((r["industry"], r.get("city") or "", r.get("state") or "") for r in leads)
    chosen = [key for key, _ in groups.most_common(args.groups)]
    by_domain = {r["normalized_domain"]: r for r in leads if r.get("normalized_domain")}
    by_name_city = {(normalize_name(r["company_name"]), (r.get("city") or "").lower()): r for r in leads}
    updates: dict[str, dict] = {}
    for number, (industry, city, state) in enumerate(chosen, 1):
        query = f"{MAPS_TERM[industry]} in {city}, {state}"
        rows = scrape(args.url.rstrip("/"), query)
        matched = 0
        for row in rows:
            target = by_domain.get(normalize_domain(row.get("website") or ""))
            if target is None:
                target = by_name_city.get((normalize_name(row.get("title") or ""), city.lower()))
            if target is None or target["industry"] != industry:
                continue
            try:
                review_count = int(float(row.get("review_count") or 0))
                rating = float(row.get("review_rating") or 0)
            except ValueError:
                continue
            if not review_count or not rating:
                continue
            distribution = _json(row.get("reviews_per_rating"), {})
            signals = _review_signals(row.get("user_reviews") or row.get("user_reviews_extended") or "")
            updates[target["company_id"]] = {
                "company_id": target["company_id"], "google_reviews": review_count, "google_rating": rating,
                "reviews_per_rating": distribution, "maps_category": row.get("category") or "",
                "place_id": row.get("place_id") or "", "google_profile_url": row.get("link") or "",
                "reviews_link": row.get("reviews_link") or "", "maps_owner": _json(row.get("owner"), {}),
                **signals,
            }
            matched += 1
        print(json.dumps({"group": number, "query": query, "results": len(rows), "matched": matched}), flush=True)
    Path(args.out).write_text(json.dumps(list(updates.values()), indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"groups": len(chosen), "matched_unique": len(updates), "out": str(Path(args.out).resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

