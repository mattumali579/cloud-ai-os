import sys
import urllib.request
import zipfile
import io
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.gh_client import get_token

sys.stdout.reconfigure(encoding='utf-8')

token = get_token()
run_id = sys.argv[1] if len(sys.argv) > 1 else '37908231078'

req = urllib.request.Request(
    f"https://api.github.com/repos/mattumali579/cloud-ai-os/actions/runs/{run_id}/logs",
    headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json', 'User-Agent': 'cloud-ai-os'}
)
resp = urllib.request.urlopen(req)
z = zipfile.ZipFile(io.BytesIO(resp.read()))
for name in z.namelist():
    text = z.read(name).decode("utf-8", "replace")
    for line in text.splitlines():
        if "error" in line.lower() or "fail" in line.lower() or "exception" in line.lower() or "traceback" in line.lower():
            print(f"{name}: {line}")
