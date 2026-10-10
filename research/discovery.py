import os
import json
import hashlib
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from research.config import BASE_DIR, DEFAULT_DB_URL

MANUAL_SOURCES_FILE = BASE_DIR / "00_SOURCE_INDEX" / "manual_sources.json"
MANIFEST_FILE = BASE_DIR / "00_SOURCE_INDEX" / "manifest.json"
DISCOVERY_LOG = BASE_DIR / "00_SOURCE_INDEX" / "discovery_log.jsonl"

def canonical_hash(url: str) -> str:
    norm = url.strip().lower()
    return hashlib.sha256(norm.encode('utf-8')).hexdigest()[:16]

def load_manifest() -> dict:
    if MANIFEST_FILE.exists():
        try:
            with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"sources": {}, "last_updated": None, "total_discovered": 0}

def save_manifest(manifest: dict):
    manifest["last_updated"] = datetime.now(timezone.utc).isoformat()
    manifest["total_discovered"] = len(manifest["sources"])
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

def log_discovery(record: dict):
    with open(DISCOVERY_LOG, "a", encoding="utf-8") as f:
        record["timestamp"] = datetime.now(timezone.utc).isoformat()
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

# Discovers from YouTube RSS channels
def discover_youtube_channels():
    # Public YouTube channel RSS feeds for business / monetization / AI automation / construction
    channels = [
        {"name": "UpFlip", "cid": "UChFahjDeMBV67DSXiF5pwBA", "niche": "Small Business & Real Business Models"},
        {"name": "Liam Ottley", "cid": "UCui4jxDaMb53Gdh-AZUTPAg", "niche": "AI Automation Agency"},
        {"name": "Alex Hormozi", "cid": "UCptAMTEiBiZad3U48VcG0TQ", "niche": "B2B Offers & Scaling"},
    ]
    
    found = []
    for ch in channels:
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['cid']}"
        req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ResearchBot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml_data = resp.read()
                root = ET.fromstring(xml_data)
                ns = {'atom': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
                for entry in root.findall('atom:entry', ns):
                    title = entry.find('atom:title', ns).text
                    vid = entry.find('yt:videoId', ns).text
                    published = entry.find('atom:published', ns).text
                    url = f"https://www.youtube.com/watch?v={vid}"
                    found.append({
                        "source_id": f"yt_{vid}",
                        "platform": "youtube",
                        "external_id": vid,
                        "url": url,
                        "title": title,
                        "creator": ch["name"],
                        "niche": ch["niche"],
                        "published_at": published,
                        "is_authorized": True
                    })
        except Exception as e:
            # Mark unavailable/blocked feed explicitly
            log_discovery({
                "source": ch["name"],
                "url": feed_url,
                "status": "DISCOVERY_ERROR",
                "error": str(e)
            })
    return found

def discover_reddit_discussions():
    subreddits = [
        {"sub": "Construction", "niche": "Construction Pain Points & Subcontracting"},
        {"sub": "contractor", "niche": "Contractor Operations & Billing"},
        {"sub": "Estimating", "niche": "Estimating & Takeoff Backlog"},
        {"sub": "sweatystartup", "niche": "Blue-Collar & Service Trades"},
        {"sub": "HVAC", "niche": "HVAC & Emergency Dispatch"},
        {"sub": "Plumbing", "niche": "Plumbing & Field Services"},
        {"sub": "PropertyManagement", "niche": "Property Management & Maintenance Triage"},
        {"sub": "CommercialRealEstate", "niche": "Real Estate & Commercial Deals"},
        {"sub": "realtors", "niche": "Real Estate & Speed to Lead"},
        {"sub": "realestateinvesting", "niche": "Real Estate Investing & Friction"},
        {"sub": "ecommerce", "niche": "E-Commerce & Digital Products"},
        {"sub": "Affiliatemarketing", "niche": "Affiliate Marketing & Content Monetization"},
        {"sub": "n8n", "niche": "AI Automation Businesses & Webhooks"},
        {"sub": "SaaS", "niche": "Micro-SaaS & Software Monetization"},
        {"sub": "Entrepreneur", "niche": "Online Businesses & Services"},
        {"sub": "SideProject", "niche": "Digital Products & SaaS"},
        {"sub": "smallbusiness", "niche": "Trades & Local Business Operations"}
    ]
    
    found = []
    for sub in subreddits:
        rss_url = f"https://www.reddit.com/r/{sub['sub']}/.rss"
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ResearchBot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                root = ET.fromstring(resp.read())
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                for entry in root.findall('atom:entry', ns):
                    title = entry.find('atom:title', ns).text
                    link_el = entry.find('atom:link', ns)
                    url = link_el.attrib.get('href') if link_el is not None else None
                    published_el = entry.find('atom:published', ns) or entry.find('atom:updated', ns)
                    published = published_el.text if published_el is not None else datetime.now(timezone.utc).isoformat()
                    content_el = entry.find('atom:content', ns)
                    raw_content = content_el.text if content_el is not None else ""
                    
                    if url and title:
                        sid = f"reddit_{canonical_hash(url)}"
                        found.append({
                            "source_id": sid,
                            "platform": "reddit",
                            "external_id": sid,
                            "url": url,
                            "title": title,
                            "creator": f"r/{sub['sub']}",
                            "niche": sub["niche"],
                            "published_at": published,
                            "raw_content": raw_content,
                            "is_authorized": True
                        })
        except Exception as e:
            log_discovery({
                "source": f"r/{sub['sub']}",
                "url": rss_url,
                "status": "DISCOVERY_ERROR",
                "error": str(e)
            })
    return found

# Reads manually supplied notes / exports / Skool community permitted inputs
def discover_manual_inputs():
    if not MANUAL_SOURCES_FILE.exists():
        # Initialize an empty manual sources template
        sample = [
            {
                "title": "Asynchronous Construction Estimating & Subcontractor AI Bidding Service",
                "url": "https://internal.research.local/case-studies/construction-estimating-b2b",
                "creator": "Matt Umali Notes (Construction B2B Opportunity)",
                "platform": "manual_notes",
                "niche": "Construction & Trades B2B",
                "content": "Specialized service: Providing automated takeoff and quantity estimating for concrete, masonry, and precast subcontractors. Subcontractors pay $1,500 - $3,000/mo retainer to have plans and architectural drawings reviewed and bid quantities extracted within 24 hours. Minimal phone calls: all submissions and takeoff deliveries happen via email and shared Drive folders. Startup capital required is under $50 (PDF parsing script, existing mini PC, existing AI tools). Low call overhead, recurring B2B retainer.",
                "claimed_earnings": "$10,000/month with 5 to 7 active trade contractor retainers."
            }
        ]
        with open(MANUAL_SOURCES_FILE, "w", encoding="utf-8") as f:
            json.dump(sample, f, indent=2, ensure_ascii=False)
            
    found = []
    try:
        with open(MANUAL_SOURCES_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
            for it in items:
                url = it.get("url", f"manual_{canonical_hash(it.get('title', ''))}")
                sid = f"manual_{canonical_hash(url)}"
                found.append({
                    "source_id": sid,
                    "platform": it.get("platform", "manual_notes"),
                    "external_id": sid,
                    "url": url,
                    "title": it.get("title", "Manual Business Source"),
                    "creator": it.get("creator", "User Community Export"),
                    "niche": it.get("niche", "General Business"),
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "raw_content": it.get("content", ""),
                    "claimed_earnings": it.get("claimed_earnings", "Unspecified"),
                    "is_authorized": True
                })
    except Exception as e:
        log_discovery({"source": "manual_inbox", "status": "ERROR", "error": str(e)})
    return found

# Discovers high-leverage business sources from sibling knowledge collections without duplicating or modifying them
def discover_sibling_knowledge_sources():
    sibling_kbs = [
        {"name": "Construction_management", "file": "03_Basics_of_contracting.md", "niche": "Construction & Contracting"},
        {"name": "Nate_Herk___AI_Automation", "file": "01_3_AI_Workflows_Step-by-Step_(Beginner's_Guide_to_n8n).md", "niche": "AI Automation Agency"},
        {"name": "Dan_Martell_AI", "file": "01_How_to_Build_an_AI_Agent_in_10_Minutes.md", "niche": "B2B SaaS & Services"},
        {"name": "Nick_Saraev", "file": "01_The_Complete_Guide_to_Making_Money_with_AI.md", "niche": "Digital Products & Content"},
        {"name": "Websites_with_Hostinger", "file": "01_How_to_Make_a_Website_and_Monetize.md", "niche": "Web Services & Affiliate"}
    ]
    
    scratch_root = Path(r"C:\Users\Admin\.gemini\antigravity\scratch\processed_notebooks")
    found = []
    for skb in sibling_kbs:
        kb_path = scratch_root / skb["name"] / "SOURCES"
        if not kb_path.exists():
            continue
        target_file = None
        # Look for exact or first available source
        for f in kb_path.glob("*.md"):
            if f.name.lower() == skb["file"].lower() or target_file is None:
                target_file = f
                if f.name.lower() == skb["file"].lower():
                    break
        if target_file and target_file.exists():
            sid = f"sibling_{skb['name']}_{canonical_hash(target_file.name)}"
            found.append({
                "source_id": sid,
                "platform": "sibling_knowledge_base",
                "external_id": sid,
                "url": f"local://sibling/{skb['name']}/{target_file.name}",
                "title": target_file.stem.replace('_', ' '),
                "creator": skb["name"].replace('_', ' '),
                "niche": skb["niche"],
                "published_at": "2026-09-01T00:00:00Z",
                "local_ref_path": str(target_file),
                "is_authorized": True
            })
    return found

def run_discovery():
    manifest = load_manifest()
    known = manifest["sources"]
    
    new_items = []
    all_candidates = []
    
    all_candidates.extend(discover_manual_inputs())
    all_candidates.extend(discover_sibling_knowledge_sources())
    all_candidates.extend(discover_youtube_channels())
    all_candidates.extend(discover_reddit_discussions())
    
    for item in all_candidates:
        sid = item["source_id"]
        if sid not in known:
            item["status"] = "DISCOVERED"
            item["discovered_at"] = datetime.now(timezone.utc).isoformat()
            known[sid] = item
            new_items.append(item)
            log_discovery({
                "source_id": sid,
                "title": item["title"],
                "url": item["url"],
                "status": "DISCOVERED"
            })
            
    save_manifest(manifest)
    return {
        "total_manifest": len(known),
        "new_discovered": len(new_items),
        "candidates": new_items
    }
