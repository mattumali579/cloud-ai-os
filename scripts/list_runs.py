import urllib.request
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.gh_client import get_token

token = get_token()
repo = 'mattumali579/cloud-ai-os'

req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/actions/runs?per_page=12",
    headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json', 'User-Agent': 'cloud-ai-os'}
)

try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        for r in data.get('workflow_runs', []):
            print(f"{r['name']}: id={r['id']} status={r['status']} conclusion={r['conclusion']} event={r['event']} created={r['created_at']}")
except Exception as e:
    print("Error:", e)
