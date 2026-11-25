"""Guards for the 2026-10-30 Baton Rouge Meta Ad Library audit.

The audit is evidence, not a scraper. These checks keep library IDs tied to
named pages and stop a later edit from turning "no ads in this search" into a
claim that a business is not advertising.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_JSON = ROOT / "docs" / "lead_engine" / "meta_ads_audit_2026-10-30" / "audit.json"
AUDIT_MD = ROOT / "docs" / "lead_engine" / "META_ADS_LOCAL_AUDIT_2026-10-30.md"

REQUIRED_PAGES = {
    "TopDogs Roofing&Sheetmetal LLC",
    "Capital City Comfort",
    "Cool Kings Heating and Air",
    "Shield Heating & Cooling",
    "Wall2Wall Construction",
    "Resolute Fitness",
    "Strength Science Studios, LLC",
    "The Well",
    "Sandbox Training, LLC.",
    "Red Stick Health and Performance",
}

FORBIDDEN = (
    "are not advertising",
    "aren't advertising",
    "is not advertising",
    "isn't advertising",
    "zero ads",
    "no ads at all",
    "never advertise",
)


def _audit() -> dict:
    return json.loads(AUDIT_JSON.read_text(encoding="utf-8"))


def test_audit_files_exist():
    assert AUDIT_JSON.is_file()
    assert AUDIT_MD.is_file()


def test_ten_named_local_pages_and_m2_left_open():
    audit = _audit()
    assert audit["observed_on"] == "2026-10-30"
    assert audit["filters"]["country"] == "United States"
    assert audit["filters"]["active_status"] == "active"
    assert audit["m2_marked_complete"] is False
    names = {page["page_name"] for page in audit["pages"]}
    assert names == REQUIRED_PAGES
    niches = [page["niche"] for page in audit["pages"]]
    assert niches.count("gym") == 5
    assert sum(niche in {"hvac", "roofing"} for niche in niches) == 5


def test_active_ads_have_permalinks_and_empty_searches_have_none():
    for page in _audit()["pages"]:
        assert page["status"] in {"ACTIVE", "NOT FOUND IN SEARCH"}
        assert page["page_id"] in page["library_url"]
        if page["status"] == "NOT FOUND IN SEARCH":
            assert page["ads"] == []
            assert page["advertiser_conclusion"] == "not verified"
            continue
        assert page["ads"], page["page_name"]
        for ad in page["ads"]:
            permalink = ad["permalink"]
            assert permalink == f"https://www.facebook.com/ads/library/?id={ad['library_id']}"
            if ad["creative_read"]:
                assert ad["started"]
                assert ad["cta"]
                assert ad["angle"]


def test_markdown_matches_ids_and_does_not_overclaim():
    audit = _audit()
    text = AUDIT_MD.read_text(encoding="utf-8")
    lowered = text.lower()
    for phrase in FORBIDDEN:
        assert phrase not in lowered
    assert "not verified" in lowered
    assert "does not mark m2 complete" in lowered
    for page in audit["pages"]:
        assert page["page_name"] in text
        assert page["page_id"] in text
        for ad in page["ads"]:
            assert ad["library_id"] in text
            assert ad["permalink"] in text
    assert "2298157767647764" in text
