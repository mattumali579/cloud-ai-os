# ARM64 (aarch64) Compatibility — Oracle A1

Oracle Always Free A1 is ARM64 (`linux/arm64`). Everything we run must have a
native arm64 build — emulation would waste the tiny CPU budget. Verified
2026-08-28 with `docker manifest inspect <image>` (grep for `arm64`) and the
PyPI JSON API for wheels.

| COMPONENT | IMAGE / TAG | ARM64 | EVIDENCE |
|---|---|---|---|
| Postgres (local dev only) | `postgres:16-alpine` | PASS | `docker manifest inspect postgres:16-alpine` lists `"architecture": "arm64", "os": "linux"` (alongside amd64, 386, arm, ppc64le, riscv64, s390x) |
| n8n | `docker.n8n.io/n8nio/n8n` | PASS | `docker manifest inspect docker.n8n.io/n8nio/n8n` lists exactly `amd64` + `arm64` linux platforms |
| Python base | `python:3.12-slim` | PASS | `docker manifest inspect python:3.12-slim` lists `"architecture": "arm64", "os": "linux"` |
| psycopg (binary + pool) | PyPI `psycopg[binary,pool]` (psycopg-binary 3.3.4 at check time) | PASS | PyPI files for `psycopg-binary` include `psycopg_binary-3.3.4-cp312-cp312-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl` (+ musllinux_1_2_aarch64) — native cp312 aarch64 wheel, no compiler needed. glibc ≥ 2.28 required; `python:3.12-slim` (Debian bookworm+, glibc ≥ 2.36) satisfies it |
| FastAPI / uvicorn[standard] | PyPI | PASS | fastapi, pydantic-core, uvicorn, httptools, uvloop, watchfiles, websockets all publish cp312 manylinux aarch64 wheels; fastapi/pyyaml/httpx/python-dotenv are pure-Python or wheel-covered. `uvicorn[standard]`'s compiled extras (uvloop, httptools) ship aarch64 wheels — worst case uvicorn falls back to asyncio/h11 pure-Python paths |
| Our image | `cloudos:*` built from `infra/docker/Dockerfile` | PASS (by construction) | Base + all wheels above are arm64-native; build with `docker buildx build --platform linux/amd64,linux/arm64 -f infra/docker/Dockerfile .` (command in the Dockerfile header) |
| Docker Engine + compose plugin | get.docker.com on Ubuntu 24.04 arm64 | PASS | Docker publishes official arm64 apt packages for Ubuntu; `bootstrap.sh` uses the official install script |

Notes:

- Re-verify on tag bumps: `docker manifest inspect <image> | grep -A2 arm64`
  (PowerShell: `docker manifest inspect <image> | Select-String arm64`).
- `postgres:16-alpine` appears in **local dev compose only**; on Oracle the DB
  is Supabase (§14) — its arm64 row matters for developers on ARM laptops.
- No dependency in pyproject.toml requires a source build on aarch64, so the
  slim base image needs **no gcc/build-essential** — keeps image small and
  builds fast on the 2-OCPU VM.
- If a future dependency lacks an aarch64 wheel, the fix is to add a build
  stage with `build-essential` in the Dockerfile — never to switch the VM to
  x86 (paid) or emulate.
