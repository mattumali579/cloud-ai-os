"""Discord message adapter. All intelligence stays behind the Cloud AI OS API."""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque

import httpx

from cloudos.config import get_settings
from cloudos.employees import role_for_channel


def _chunks(text: str, limit: int = 1900) -> list[str]:
    text = text.strip() or "No output returned."
    return [text[i : i + limit] for i in range(0, len(text), limit)]


class CloudOSClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}

    async def invoke_employee(self, role: str, message: str, history: list[str]) -> dict:
        async with httpx.AsyncClient(timeout=360) as client:
            response = await client.post(
                f"{self.base_url}/v1/employees/{role}/invoke",
                headers=self.headers,
                json={"message": message, "history": history, "privacy_label": "internal"},
            )
            response.raise_for_status()
            return response.json()

    async def generate_higgsfield(self, kind: str, prompt: str) -> dict:
        async with httpx.AsyncClient(timeout=960) as client:
            response = await client.post(
                f"{self.base_url}/v1/higgsfield/generate",
                headers=self.headers,
                json={"kind": kind, "prompt": prompt, "confirmed": True},
            )
            response.raise_for_status()
            return response.json()

    async def status(self) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.base_url}/v1/providers?probe=true", headers=self.headers
            )
            response.raise_for_status()
            return response.json()

    async def preview_email(self, draft_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/v1/email/preview",
                headers=self.headers,
                json={"draft_id": draft_id},
            )
            response.raise_for_status()
            return response.json()

    async def send_email(self, draft_id: str, fingerprint: str) -> dict:
        async with httpx.AsyncClient(timeout=360) as client:
            response = await client.post(
                f"{self.base_url}/v1/email/send",
                headers=self.headers,
                json={"draft_id": draft_id, "fingerprint": fingerprint},
            )
            response.raise_for_status()
            return response.json()


def _error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            error = exc.response.json().get("error", {})
            return f"{error.get('code', 'ERROR')}: {error.get('message', 'request failed')}"
        except Exception:  # noqa: BLE001
            return f"Cloud AI OS returned HTTP {exc.response.status_code}."
    if isinstance(exc, httpx.ConnectError):
        return "Cloud AI OS is offline. Start the API, then try again."
    return f"Request failed: {type(exc).__name__}"


def main() -> None:
    try:
        import discord
    except ImportError as exc:
        raise SystemExit("Discord support is not installed. Run: pip install -e .[discord]") from exc

    settings = get_settings()
    if not settings.discord_bot_token:
        raise SystemExit("DISCORD_BOT_TOKEN is missing from .env")
    if not settings.agent_api_token:
        raise SystemExit("AGENT_API_TOKEN is missing from .env")

    api = CloudOSClient(settings.discord_api_url, settings.agent_api_token)
    histories: dict[int, deque[str]] = defaultdict(lambda: deque(maxlen=8))
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"Cloud AI OS Discord bot ready as {client.user}")

    @client.event
    async def on_message(message):
        if message.author.bot or not message.guild:
            return
        if settings.discord_allowed_guild_id and str(message.guild.id) != settings.discord_allowed_guild_id:
            return

        content = message.content.strip()
        if not content:
            return

        if content == "!status":
            try:
                payload = await api.status()
                live = payload.get("probe") or payload.get("providers") or []
                lines = [
                    f"{p.get('provider')}: {p.get('state')} ({p.get('auth_mode') or 'no auth'})"
                    for p in live
                ]
                await message.reply("\n".join(lines) or "No providers configured.")
            except Exception as exc:  # noqa: BLE001
                await message.reply(_error_text(exc))
            return

        if content.startswith("!higgsfield "):
            parts = content.split(maxsplit=3)
            if len(parts) < 4 or parts[1].lower() != "confirm" or parts[2].lower() not in ("image", "video"):
                await message.reply(
                    "Generation uses Higgsfield credits. To run it explicitly: "
                    "`!higgsfield confirm image <prompt>` or `!higgsfield confirm video <prompt>`."
                )
                return
            try:
                async with message.channel.typing():
                    payload = await api.generate_higgsfield(parts[2].lower(), parts[3])
                for chunk in _chunks(payload.get("output", "")):
                    await message.reply(chunk)
            except Exception as exc:  # noqa: BLE001
                await message.reply(_error_text(exc))
            return

        if content.startswith("!email preview "):
            draft_id = content.split(maxsplit=2)[2]
            try:
                payload = await api.preview_email(draft_id)
                lines = [
                    f"Email draft `{payload['draft_id']}` — {payload['count']} recipients",
                    f"Confirmation fingerprint: `{payload['fingerprint']}`",
                ]
                lines.extend(
                    f"- {row['to']} — {row['subject']}" for row in payload.get("messages", [])
                )
                lines.append(
                    f"To send exactly this draft: `!email confirm {payload['draft_id']} {payload['fingerprint']}`"
                )
                await message.reply("\n".join(lines))
            except Exception as exc:  # noqa: BLE001
                await message.reply(_error_text(exc))
            return

        if content.startswith("!email confirm "):
            parts = content.split(maxsplit=3)
            if len(parts) != 4:
                await message.reply("Use: `!email confirm <draft-id> <fingerprint>`")
                return
            try:
                payload = await api.send_email(parts[2], parts[3])
                await message.reply(f"Sent {payload['sent_count']} emails from draft `{payload['draft_id']}`.")
            except Exception as exc:  # noqa: BLE001
                await message.reply(_error_text(exc))
            return

        role = role_for_channel(message.channel.name)
        if role is None:
            return
        history = list(histories[message.channel.id])
        histories[message.channel.id].append(f"OWNER: {content}")
        try:
            async with message.channel.typing():
                payload = await api.invoke_employee(role, content, history)
            answer = payload.get("text") or "No output returned."
            histories[message.channel.id].append(f"{role.upper()}: {answer[:2500]}")
            for chunk in _chunks(answer):
                await message.reply(chunk)
                await asyncio.sleep(0.2)
        except Exception as exc:  # noqa: BLE001
            await message.reply(_error_text(exc))

    client.run(settings.discord_bot_token, log_handler=None)
