# Discord → Cloud AI OS → Codex employees

This adapter uses the locally installed Codex CLI and **Sign in with ChatGPT**.
It does not use the OpenAI API, Anthropic, Claude, or an Anthropic API key.
Codex plan limits still apply. Higgsfield generations use Higgsfield credits.

## What works

Create Discord text channels with these names:

| Channel | Employee output |
|---|---|
| `researcher` | Current, sourced research with dates and next actions |
| `content-strategist` | Positioning, pillars, formats, 14-day calendar, KPIs |
| `product-builder` | Offer, validation, economics, build and launch plan |
| `email-marketer` | Full campaigns, sequences, copy, triggers, metrics |
| `analyst` | Scorecard, diagnosis, decisions, measurement plan |
| `higgsfield-producer` | Creative brief, shot list, prompts, CLI command |

Messages in employee channels are enriched with relevant files from
`SECOND_BRAIN_PATH`. The research, strategy, and product roles run Codex with
web search enabled. Every employee invocation forces `model_hint=codex`, so it
cannot fall through to Claude or Gemini.

## One-time Windows setup

1. Install Python 3.11+, Node.js, Codex CLI, and clone this repository.
2. In PowerShell from the repository folder, run:

   ```powershell
   .\scripts\setup_discord_bot_windows.ps1
   codex
   ```

3. The first Codex run must use **Sign in with ChatGPT**. Do not use an API key.
4. In the Discord Developer Portal, create a bot, enable **Message Content
   Intent**, invite it with View Channels, Send Messages, Read Message History,
   and Attach Files permissions, then copy its token.
5. Edit `.env`:

   ```dotenv
   AGENT_API_TOKEN=replace-with-a-long-random-local-secret
   DISCORD_BOT_TOKEN=replace-with-your-discord-token
   DISCORD_ALLOWED_GUILD_ID=your-server-id
   SUBSCRIPTION_PROVIDERS=codex
   SECOND_BRAIN_PATH=C:\path\to\AI-Second-Brain
   CODEX_WORKSPACE=C:\path\to\AI-Second-Brain
   ```

6. Start the API and Discord bot together:

   ```powershell
   .\.venv\Scripts\python.exe -m cloudos.discord_bot.launcher
   ```

Keep this terminal and the mini PC running. Type `!status` in Discord to verify
that Codex reports `AVAILABLE_SUBSCRIPTION` and `chatgpt_subscription`.

## Higgsfield

Install and authenticate the official CLI, then install its Codex skills inside
the AI Second Brain folder:

```powershell
npm install -g @higgsfield/cli
higgsfield auth login
cd C:\path\to\AI-Second-Brain
npx skills add higgsfield-ai/skills
```

Higgsfield is off by default. After reviewing its credit usage, set:

```dotenv
HIGGSFIELD_ALLOW_GENERATION=true
```

Generation only runs with one of these explicit messages:

```text
!higgsfield confirm image your complete prompt
!higgsfield confirm video your complete prompt
```

The bridge uses fixed supported commands, passes the prompt as a single process
argument, never invokes a shell, and refuses all unconfirmed generation.

## Important boundaries

- The bot can create plans, copy, briefs, research, and workspace artifacts
  through Codex. It cannot publish, send email, buy inventory, or make external
  account changes unless a separate approved integration is added.
- The Discord token is the only new secret required by the bot. Never commit
  `.env`.
- If Codex reports a plan limit, Cloud AI OS stops. It never switches to a
  metered API.

## Hostinger Email

The email marketer can prepare a maximum of 10 individually addressed emails
in `email_outbox/pending/<draft-id>.json`. Add the Hostinger mailbox credentials
to `.env`; they are used only by the local SMTP connection and are never placed
in a Codex prompt:

```dotenv
HOSTINGER_SMTP_HOST=smtp.hostinger.com
HOSTINGER_SMTP_PORT=465
HOSTINGER_SMTP_USERNAME=you@yourdomain.com
HOSTINGER_SMTP_PASSWORD=your-mailbox-password
EMAIL_FROM_NAME=Your Brand
EMAIL_SEND_ENABLED=false
EMAIL_OUTBOX_PATH=C:\path\to\AI-Second-Brain\email_outbox
```

Ask the email marketer to create the 10-email draft, then review the exact
recipient and subject list in Discord:

```text
!email preview launch-01
```

The preview returns a fingerprint. Only after checking the recipient identities
and copy should you enable sending and use the exact confirmation command:

```dotenv
EMAIL_SEND_ENABLED=true
```

```text
!email confirm launch-01 12-character-fingerprint
```

The same exact draft cannot be attempted twice. A receipt is written after each
delivery so a partial SMTP failure does not silently resend earlier recipients.
