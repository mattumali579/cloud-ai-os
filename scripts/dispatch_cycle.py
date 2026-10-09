import urllib.request
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.gh_client import get_token

token = get_token()
repo = 'mattumali579/cloud-ai-os'

req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/actions/workflows/outreach-send.yml/dispatches",
    method="POST",
    data=json.dumps({"ref": "master", "inputs": {"mode": "cycle"}}).encode(),
    headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "cloud-ai-os"
    }
)
try:
    with urllib.request.urlopen(req) as resp:
        print("Dispatched outreach-send with mode=cycle:", resp.status)
except urllib.error.HTTPError as e:
    print("HTTP Error:", e.code, e.read().decode())
