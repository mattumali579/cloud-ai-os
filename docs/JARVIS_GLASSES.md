# Jarvis glasses bridge

This is a private, outbound-only bridge from the Jarvis Windows PC to Visor on the Samsung phone. It does not expose a port on the PC, use n8n, or run paid inference.

## Verified VDK contract

The implementation follows Visor VDK 0.1.1 and its shipped OpenRPC schema:

- subscribe to `voice.requests` with `{"phrases":["jarvis"]}` so Visor's regular model cannot answer commands intended for the mini PC;
- receive a request containing `id`, `text`, and `phrase`;
- call `voice.reply` with `request`, `text`, and optional `listen` within about 20 seconds;
- route commands Jarvis does not handle instantly to the existing private host-worker task queue automatically;
- say `task status` or `cancel task` for queued mini-PC work; the user never needs to name the internal model;
- optionally subscribe to `voice.transcripts` for a private `Hey Jarvis` hotword while Visor remains visible on the phone;
- call `glasses.status` using the `state` scope.

The program needs only the `voice` and `state` scopes. It searches file names only under configured folders. It cannot execute arbitrary voice commands.

## Phone setup required

On the S22 in Visor, open **Settings → Apps → Programs and agents**. Enable program connections and local-network access, then create a program named `Jarvis` with `voice` and `state`. Note the private IP shown by Visor and copy the program token.

On the Jarvis PC:

```powershell
cd C:\Users\Admin\cloud-ai-os
.\.venv\Scripts\python.exe .\scripts\jarvis_glasses.py configure
.\.venv\Scripts\python.exe .\scripts\jarvis_glasses.py probe
```

The token is hidden while entered and stored in Windows Credential Manager. The JSON config contains only the private phone URL, phrase, and approved search folders.

After the probe succeeds, register the always-on task:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\register-jarvis-glasses-task.ps1
```

Then say: **“Hey Meta, ask Jarvis for status.”**

The end-to-end test passes only after the glasses speak the reply and `logs/jarvis-glasses.log` records the matching request.
