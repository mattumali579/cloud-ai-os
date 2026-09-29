"""Normalization used by every dedupe decision. Pure functions, no I/O."""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Hosts where many unrelated businesses share one domain; the first path
# segment identifies the business instead (facebook.com/joesroofing).
SHARED_HOSTS = {
    "facebook.com", "m.facebook.com", "instagram.com", "linktr.ee", "sites.google.com",
    "business.site", "wixsite.com", "godaddysites.com", "square.site", "weebly.com",
    "wordpress.com", "blogspot.com", "yelp.com", "nextdoor.com", "google.com", "g.page",
    "youtube.com", "tiktok.com", "x.com", "twitter.com", "linkedin.com", "mystrikingly.com",
    "carrd.co", "webflow.io", "myshopify.com", "square.com", "vagaro.com", "mindbodyonline.com",
}

# Directories / aggregators / marketplaces: never a business of their own.
DIRECTORY_DOMAINS = {
    "yelp.com", "angi.com", "angieslist.com", "homeadvisor.com", "thumbtack.com", "bbb.org",
    "yellowpages.com", "manta.com", "houzz.com", "porch.com", "nextdoor.com", "mapquest.com",
    "superpages.com", "chamberofcommerce.com", "buildzoom.com", "networx.com", "bark.com",
    "expertise.com", "zocdoc.com", "healthgrades.com", "opencare.com", "classpass.com",
    "groupon.com", "tripadvisor.com", "foursquare.com", "cylex.us.com", "birdeye.com",
    "google.com", "apple.com", "bing.com", "linkedin.com", "indeed.com", "glassdoor.com",
    "carfax.com", "repairpal.com", "cars.com", "local.com", "hotfrog.com", "brownbook.net",
}

FREEMAIL = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com", "me.com",
    "live.com", "msn.com", "att.net", "sbcglobal.net", "bellsouth.net", "cox.net", "comcast.net",
    "verizon.net", "charter.net", "earthlink.net", "protonmail.com", "proton.me", "ymail.com",
    "mail.com", "gmx.com", "zoho.com", "rocketmail.com", "windstream.net", "centurylink.net",
    "frontier.com", "suddenlink.net", "embarqmail.com", "q.com",
}

_TWO_LEVEL_SUFFIXES = {"co.uk", "com.au", "co.nz", "com.mx", "com.br", "co.za", "org.uk", "ac.uk"}

_NAME_NOISE = re.compile(
    r"\b(llc|l\.l\.c|inc|incorporated|co|company|corp|corporation|ltd|limited|pllc|plc|lp|llp|pc|dds|dmd|the|and)\b"
)


def _host(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    try:
        host = (urlparse(raw).hostname or "").lower().strip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def registrable_domain(host: str) -> str:
    parts = [p for p in str(host or "").lower().split(".") if p]
    if len(parts) < 2:
        return ""
    if ".".join(parts[-2:]) in _TWO_LEVEL_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def normalize_domain(website_or_domain: str) -> str:
    """Company identity key from a website URL or bare domain.

    Ordinary sites -> registrable domain (``https://www.Roof.com/x`` -> ``roof.com``).
    Shared hosts   -> host + first path segment (``facebook.com/joesroofing``).
    """
    raw = str(website_or_domain or "").strip()
    host = _host(raw)
    if not host or "." not in host:
        return ""
    reg = registrable_domain(host)
    if reg in FREEMAIL or host in FREEMAIL:
        return ""  # an email provider is never a company's identity
    if reg in SHARED_HOSTS or host in SHARED_HOSTS:
        if "://" not in raw:
            raw = "http://" + raw
        path = [seg for seg in (urlparse(raw).path or "").split("/") if seg]
        sub = host.split(".")[0] if reg in {"wixsite.com", "godaddysites.com", "square.site", "weebly.com",
                                             "wordpress.com", "blogspot.com", "business.site", "webflow.io",
                                             "myshopify.com", "mystrikingly.com"} and host != reg else ""
        key = sub or (path[0].lower() if path else "")
        return f"{reg}/{key}" if key else ""
    return reg


def is_shared_host(website: str) -> bool:
    return registrable_domain(_host(website)) in SHARED_HOSTS


def is_directory(website: str) -> bool:
    return registrable_domain(_host(website)) in DIRECTORY_DOMAINS


def normalize_name(name: str) -> str:
    text = str(name or "").lower().replace("&", " and ")
    text = re.sub(r"[’'`]", "", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = _NAME_NOISE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,24}$")


def normalize_email(email: str) -> str:
    value = str(email or "").strip().lower()
    value = value.removeprefix("mailto:").split("?")[0].strip().strip(".,;:()<>[]\"'")
    return value if _EMAIL_RE.match(value) else ""


def email_domain(email: str) -> str:
    return normalize_email(email).partition("@")[2]


def normalize_state(state: str) -> str:
    value = str(state or "").strip()
    if len(value) == 2:
        return value.upper()
    return US_STATES.get(value.lower(), value[:2].upper() if value else "")


US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


def city_state_from_address(address: str) -> tuple[str, str]:
    """'434 S Acadian Thruway, Baton Rouge, LA 70806' -> ('Baton Rouge', 'LA')."""
    parts = [p.strip() for p in str(address or "").split(",") if p.strip()]
    for i in range(len(parts) - 1, 0, -1):
        m = re.match(r"^([A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?$", parts[i])
        if m:
            return parts[i - 1], m.group(1)
    return "", ""
