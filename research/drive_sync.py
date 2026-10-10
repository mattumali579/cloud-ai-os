import os
import json
import urllib.request
import urllib.parse
from pathlib import Path
from research.config import BASE_DIR, DRIVE_SUBFOLDERS, DRIVE_ROOT_ID

TOKEN_PATH_WINDOWS = Path(r"C:\Users\Admin\.gemini\antigravity\plugin_data\gdrive\token.json")
TOKEN_PATH_LINUX = Path("/home/cloudos/.gemini/antigravity/plugin_data/gdrive/token.json")

def get_token_file():
    if TOKEN_PATH_WINDOWS.exists():
        return TOKEN_PATH_WINDOWS
    if TOKEN_PATH_LINUX.exists():
        return TOKEN_PATH_LINUX
    # Fallback search
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    cand = home / ".gemini" / "antigravity" / "plugin_data" / "gdrive" / "token.json"
    if cand.exists():
        return cand
    raise FileNotFoundError("Could not find Google Drive token.json")

def get_access_token():
    tfile = get_token_file()
    with open(tfile, "r", encoding="utf-8") as f:
        tdata = json.load(f)
    
    client_id = tdata["client_id"]
    client_secret = tdata["client_secret"]
    refresh_token = tdata["token"]["refresh_token"]

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }).encode("utf-8")

    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res["access_token"]

def upload_file_to_drive(local_filepath: Path, subfolder_name: str, mime_type: str = "text/markdown"):
    parent_id = DRIVE_SUBFOLDERS.get(subfolder_name, DRIVE_ROOT_ID)
    token = get_access_token()
    
    filename = local_filepath.name
    content_bytes = local_filepath.read_bytes()

    # Multipart upload
    boundary = "-------314159265358979323846"
    delimiter = f"\r\n--{boundary}\r\n".encode("utf-8")
    close_delim = f"\r\n--{boundary}--\r\n".encode("utf-8")

    metadata = {
        "name": filename,
        "parents": [parent_id]
    }

    body = (
        delimiter +
        b'Content-Type: application/json; charset=UTF-8\r\n\r\n' +
        json.dumps(metadata).encode("utf-8") +
        delimiter +
        f'Content-Type: {mime_type}\r\n\r\n'.encode("utf-8") +
        content_bytes +
        close_delim
    )

    req = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
            "Content-Length": str(len(body))
        }
    )

    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res.get("id")

SYNC_CACHE_FILE = BASE_DIR / "07_VERIFICATION_AND_LOGS" / ".drive_sync_cache.json"

def load_sync_cache() -> dict:
    if SYNC_CACHE_FILE.exists():
        try:
            with open(SYNC_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_sync_cache(cache: dict):
    try:
        SYNC_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SYNC_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass

def sync_subfolder_to_drive(subfolder_name: str):
    folder_dir = BASE_DIR / subfolder_name
    if not folder_dir.exists():
        return []

    cache = load_sync_cache()
    uploaded = []
    
    for f in folder_dir.glob("*"):
        if f.is_file() and not f.name.startswith("."):
            mtime = f.stat().st_mtime
            cache_key = f"{subfolder_name}/{f.name}"
            cached = cache.get(cache_key)
            
            if cached and cached.get("mtime") == mtime and cached.get("drive_id"):
                uploaded.append({"file": f.name, "drive_id": cached.get("drive_id"), "status": "CACHED"})
                continue
                
            mime = "application/json" if f.suffix == ".json" else "text/markdown"
            try:
                fid = upload_file_to_drive(f, subfolder_name, mime_type=mime)
                cache[cache_key] = {"drive_id": fid, "mtime": mtime}
                uploaded.append({"file": f.name, "drive_id": fid, "status": "UPLOADED"})
            except Exception as e:
                print(f"Error uploading {f.name} to {subfolder_name}: {e}")
                
    save_sync_cache(cache)
    return uploaded

