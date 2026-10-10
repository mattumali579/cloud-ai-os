import os
from pathlib import Path

# Paths
BASE_DIR = Path(os.environ.get("RESEARCH_BASE_DIR", r"C:\Users\Admin\.gemini\antigravity\scratch\ONLINE_MONEY_RESEARCH"))
if not BASE_DIR.exists():
    # If running in Docker container
    alt = Path("/workspace/data/ONLINE_MONEY_RESEARCH")
    if alt.exists():
        BASE_DIR = alt
    else:
        # Fallback to local scratch
        BASE_DIR.mkdir(parents=True, exist_ok=True)

SUBDIRS = [
    "00_SOURCE_INDEX",
    "01_RAW_AND_TRANSCRIPTS",
    "02_EXTRACTED_KNOWLEDGE",
    "03_SUMMARIES",
    "04_BUSINESS_MODELS",
    "05_CROSS_SOURCE_ANALYSIS",
    "06_ACTIONABLE_OPPORTUNITIES",
    "07_VERIFICATION_AND_LOGS"
]

for sd in SUBDIRS:
    (BASE_DIR / sd).mkdir(parents=True, exist_ok=True)

# Google Drive Folders
DRIVE_ROOT_ID = "1G0ibojBA0RcA7cmSPtg_s24LBwgOL3ds"
DRIVE_SUBFOLDERS = {
    "00_SOURCE_INDEX": "1PMtv0AP_U7LjSKrmlG5LbklsZ1h364z1",
    "01_RAW_AND_TRANSCRIPTS": "1GnK_fV_wxXbsSfhuzrtGd3_RLpFrnFh3",
    "02_EXTRACTED_KNOWLEDGE": "1ozvkHvYNuAIcHtfd87_jhs79rs0uL-D3",
    "03_SUMMARIES": "1I2CZ-eNaioMgASFMBMMAHPwSRn_QhqSl",
    "04_BUSINESS_MODELS": "1qQeOVD-sb62MuuZyPWQMJK0MHlDDxQ9w",
    "05_CROSS_SOURCE_ANALYSIS": "15ieCh8iFRMCTAzUwqDtUFf8nkKNmXNbS",
    "06_ACTIONABLE_OPPORTUNITIES": "12tmQx9wy55TKOj-UJB7PszThLahoi4xf",
    "07_VERIFICATION_AND_LOGS": "1iXjAfZKzD540SScMvXjf1CfHnBtmGu8x"
}

# User Profile Settings
USER_PROFILE = {
    "capital_available_usd": 100.0,
    "hardware": "Windows mini PC",
    "tooling": ["existing AI tools", "Python", "Docker", "n8n"],
    "domain_knowledge": "construction, masonry, bidding, subcontracting",
    "preference": "minimal phone calls, asynchronous, productized services, automation",
    "income_goal_monthly_usd": 10000.0
}

# Postgres URL
DEFAULT_DB_URL = os.environ.get(
    "DATABASE_URL", 
    "postgresql://cloudos:cloudos@127.0.0.1:5432/cloudos"
)
