import sys
import urllib.request
import zipfile
import io
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.gh_client import get_token

sys.stdout.reconfigure(encoding='utf-8')

token = get_token()
run_id = sys.argv[1] if len(sys.argv) > 1 else '37907273675'

req = urllib.request.Request(
    f"https://api.github.com/repos/mattumali579/cloud-ai-os/actions/runs/{run_id}/logs",
    headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json', 'User-Agent': 'cloud-ai-os'}
)
resp = urllib.request.urlopen(req)
z = zipfile.ZipFile(io.BytesIO(resp.read()))
for name in ["send/8_Run.txt", "send/9_Summary (counts only).txt"]:
    if name in z.namelist():
        print(f"=== {name} ===")
        print(z.read(name).decode("utf-8", "replace"))
