# Jarvis glasses archive

Events from a vision session can be encrypted and copied to the owner's existing Google Drive. Raw frames stay off unless that session opts in. This document does not contain a folder id, an OAuth token, or a passphrase.

The private Drive folder is the one named **Jarvis Vision Archive** at the My Drive root. Its id stays in Drive and in the owner's rclone config. It is not written in this repository.

## What is not done yet

This cloud agent is not the Windows host. It did not see Google Drive for desktop, and `rclone` is not installed here. No file was uploaded. An authenticated upload is a separate step from these offline tests, and both are separate from a physical glasses session.

## One-time setup on the Windows host

rclone's shared Google client is being retired during 2026. Create your own OAuth client in a Google Cloud project with the Drive API enabled, and do not turn on billing.

1. Install rclone from https://rclone.org/downloads/.
2. Run `rclone config` and make a `drive` remote with that client id and secret. Approve it once in the browser when rclone asks. Do not type the Google password into chat or into this repo.
3. Make a second remote of type `crypt` (https://rclone.org/crypt/) whose remote path is the drive remote plus `Jarvis Vision Archive`. Choose a passphrase. rclone will encrypt names and contents. The Drive website will show opaque files. Read them only through this crypt remote.
4. Put the passphrase in a password manager and in `rclone.conf`. `rclone.conf` stays in the user profile, not in Git. Losing the passphrase means those objects cannot be read. There is no recovery.
5. In the user environment for `CloudAIOS-HostWorker`, set:
   - `GLASSES_ARCHIVE_RCLONE_REMOTE` to the crypt remote, for example `visioncrypt:`
   - `GLASSES_ARCHIVE_PASSPHRASE` to a second passphrase used before the bytes are handed to rclone
   - `GLASSES_ARCHIVE_SPOOL` only if the default below is wrong

Do not set those variables in the GitHub Actions workflow.

## Local spool

Default directory: `%USERPROFILE%\.local\share\cloud-ai-os\glasses-archive`

It is a queue, not a video library. The cap is 64 MiB. Events are stored. Photos and clips are refused unless the session is opened with media opt-in, and even then a single clip stops at 2 MiB. Face templates are refused. The phone keeps only Visor's own transient buffer.

Closed chunks are gzip JSONL, then encrypted, then the plaintext is deleted. The host worker job `glasses.archive.drain` copies each ciphertext file with `rclone copyto` (not `sync`). After every remote hash matches, the local ciphertext is removed. A failed copy, a checksum mismatch, expired auth, or a full Drive leaves the local copy in place. Remote objects are never deleted by this job.

The searchable index is `index.jsonl` in that spool. It stores session id, time, labels, and track ids. It does not store frames or landmark coordinates. It can be rebuilt from committed session state. It is not a SQLite file on a Drive-synced folder.

## Layout

```text
sessions/YYYY/MM/DD/<session_uuid>/events-0001.jsonl.gz.enc
sessions/YYYY/MM/DD/<session_uuid>/summary.json.enc
sessions/YYYY/MM/DD/<session_uuid>/manifest.json.enc
sessions/YYYY/MM/DD/<session_uuid>/media/<name>.enc   # only after opt-in
```

Event schema `glasses.archive.event.v1` has UTC time, session and event ids, source, model name and version, class, confidence, normalized boxes, session track ids, an optional 21-point landmark list, selection method, last-seen and lost, frame counts, and optional motion with a unit, a calibrated flag, and a confidence. It does not store distance, speed, or a permanent object id.

## Restore

Outer layer, through the crypt remote:

```text
rclone copy visioncrypt:sessions/2026/11/16/<session_uuid> C:\restore\<session_uuid>
rclone cryptcheck C:\restore\<session_uuid> visioncrypt:sessions/2026/11/16/<session_uuid> --one-way
```

Inner layer, with the same passphrase the spool used:

```text
python -m cloudos.glasses.archive.decrypt C:\restore\<session_uuid> C:\restore\plain
```

Then decrypt the inner layer. The passphrase is the environment variable, not a file in Git:

```text
set GLASSES_ARCHIVE_PASSPHRASE=the-passphrase-from-your-password-manager
python -m cloudos.glasses.archive C:\restore\<session_uuid> C:\restore\plain
```

Compare each plaintext SHA-256 with `manifest.json`.

## Cost and failures

Drive space comes from the existing 5 TB plan. rclone is free. A Google Cloud project used only for an OAuth client should stay on the free tier. This path does not buy storage and does not call OpenAI.

| Condition | What happens |
|---|---|
| Remote auth expired | Drain stops, sets alert `auth_expired`, keeps the spool. Run `rclone config reconnect` on the drive remote. |
| Drive is full | Alert `storage_full`. Nothing local is deleted. |
| Queued spool is over half of 64 MiB | Alert `queue_over_threshold`. Uploads still try. New events stop at 64 MiB instead of dropping old ones. |
| Checksum mismatch | Local session stays. Fix the remote copy before retrying. |
| Passphrase lost | That session cannot be decrypted. |

## Real upload, when authorized

On the Windows host, after the steps above: write one sample tracking event, encrypt it, `glasses.archive.drain` once, `rclone cat` the object back, decrypt, and compare SHA-256 with the manifest. Only then should the queue directory for that session be gone. That result is an authenticated Drive test. It is not a glasses end-to-end test.
