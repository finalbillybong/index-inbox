# Index Inbox

Index Inbox is a private, self-hosted capture and organization service for Pebble Index 01 recordings. The Pebble mobile app transcribes a recording and sends it to Index Inbox through an authenticated HTTPS webhook. Notes and optional audio remain on storage you control.

## Highlights

- Flexible JSON and multipart webhook ingestion
- Header-based webhook authentication
- Choice of fully local authentication or Firebase Email/Password authentication
- SQLite metadata and local audio storage
- Retry deduplication and delivery activity history
- Editable transcriptions, tags, categories, starring and archiving
- Explicit voice-created Collections with combined presentation
- Chronological Collection timelines with editing, audio, completion, and Collection-scoped exports
- Conservative, user-confirmed suggestions for near-matching Collection identifiers
- Automatic background refresh with live, dismissible capture notices
- Search, filters, pagination and bulk actions
- Original webhook payload inspection
- Audio playback, speed controls, downloads and retention cleanup
- JSON, Markdown and ZIP/audio exports
- Verified restorable backups with manifests, status, retention, and optional external hook
- Installable responsive PWA with manual text/audio capture
- Native Android app with offline capture, instant self-hosted notifications and signed updates
- Native Android home-screen widget for two-tap audio capture
- Prebuilt server image published through GitHub Container Registry
- CPU-local transcription of browser recordings with editable previews
- Cached recent entries and mobile share-target support

## Architecture

```text
Index 01 ring
      |
      v
Pebble mobile app (recording and transcription)
      |
      | HTTPS webhook + secret header
      v
Index Inbox on your server
      |-- SQLite database
      `-- optional audio files
```

The ring does not communicate with Index Inbox directly. The Pebble mobile app remains the bridge between the ring and your server.

## Choosing authentication

Index Inbox supports two authentication modes:

| Mode | Choose it when | Tradeoff |
| --- | --- | --- |
| `firebase` | You want managed, low-maintenance email/password authentication and do not require the complete login path to remain local | Account identity and authentication depend on Google's Firebase service; notes, transcriptions and audio still remain on your server |
| `local` | You want Index Inbox to authenticate users without contacting an external identity provider | You are responsible for securely operating, updating and recovering the self-hosted service |

Firebase is the easier option for users who prefer a managed authentication service. Local authentication uses Argon2id password hashes, server-side sessions, CSRF protection, login throttling and a protected first-run setup flow, but its security also depends on keeping Index Inbox, Docker and the reverse proxy up to date.

Existing installations continue to use Firebase when `AUTH_PROVIDER` is omitted. New installations should choose a mode explicitly.

Index Inbox is a single-user/shared-instance application. Multiple credentials
can be created, but every authenticated account sees the same inbox and has the
same trusted administrative access; there are no per-user inboxes or roles.

## Requirements

- Docker Engine with Docker Compose
- A public HTTPS hostname through a reverse proxy or secure tunnel
- For Firebase mode only: a Firebase project and service-account JSON file

## Quick start: local authentication

The published image includes the web application, API and matching signed Android APK. A new server needs Docker Compose, an HTTPS origin and a persistent data directory; it does not need Git, Python, Java or the Android SDK.

Run the setup helper with the public HTTPS origin and absolute data path:

```bash
curl -fsSL https://raw.githubusercontent.com/finalbillybong/index-inbox/main/setup.sh \
  | bash -s -- https://index.example.com /absolute/path/to/index-inbox/data
```

The helper downloads `compose.yaml`, generates independent webhook and setup secrets, creates `.env`, pulls the latest GHCR image and starts the service. It prints the one-time setup token.

Open the HTTPS origin and create the first account using that token, a username and a password of at least 12 characters. Then remove the `LOCAL_SETUP_TOKEN` line from `.env` and recreate the container:

```bash
docker compose up -d --force-recreate
```

Check local container health with:

   ```bash
   curl http://127.0.0.1:5050/health
   ```

   A healthy service responds with `{"ok":true}`.

The host exposes port `5050`; the container listens on port `8080`. Point a reverse proxy or secure tunnel at `http://SERVER_ADDRESS:5050` and terminate HTTPS before exposing the application publicly. Do not forward port `5050` directly from a router.

## Configuration

| Variable | Required | Description |
| --- | --- | --- |
| `WEBHOOK_SECRET` | Yes | Initial secret used to authenticate incoming Index webhooks; an in-app rotation persists in `/data` and takes precedence |
| `AUTH_PROVIDER` | Yes | `local` for self-hosted accounts or `firebase` for Firebase Authentication |
| `AUTH_ALLOWED_ORIGINS` | Local | Comma-separated allowed browser origins, including scheme and port |
| `AUTH_EXPECTED_ORIGIN` | Local | Deprecated single-origin setting retained for compatibility |
| `AUTH_COOKIE_SECURE` | Local | Keep `true` in production; set `false` only for localhost HTTP testing |
| `AUTH_SESSION_DAYS` | Local | Absolute local-session lifetime, default 30 days |
| `AUTH_IDLE_DAYS` | Local | Local-session idle timeout, default 7 days |
| `AUTH_DEVICE_DAYS` | Local | Lifetime of a native-app device token, default 90 days |
| `TRUSTED_PROXY_HOPS` | No | Number of trusted forwarding hops; defaults to `0`, which ignores forwarded client-IP headers |
| `TRUSTED_PROXY_CIDRS` | Proxy trust | Comma-separated IP networks allowed to supply forwarded client addresses |
| `LOCAL_SETUP_TOKEN` | Local setup | One-time secret required to create the first account through the browser |
| `FIREBASE_PROJECT_ID` | Firebase | Firebase project identifier |
| `FIREBASE_API_KEY` | Firebase | Firebase web application API key |
| `FIREBASE_AUTH_DOMAIN` | Firebase | Usually `PROJECT_ID.firebaseapp.com` |
| `ALLOWED_EMAILS` | Recommended | Comma-separated lowercase email allowlist |
| `REQUIRE_VERIFIED_EMAIL` | Recommended | Require Firebase's `email_verified` claim |
| `INDEX_DATA_PATH` | Yes | Persistent host directory for SQLite and audio |
| `FIREBASE_CREDENTIALS_PATH` | Firebase | Host path to the service-account JSON file |
| `BACKUP_HOOK_URL` | No | Automation endpoint called from the backup control |
| `TRANSCRIPTION_ENABLED` | No | Enable server-local transcription of browser recordings; defaults to `true` |
| `TRANSCRIPTION_MODEL` | No | faster-whisper model name; defaults to `tiny.en` |
| `TRANSCRIPTION_LANGUAGE` | No | Language code used for transcription; defaults to `en`, or leave empty for detection |
| `TRANSCRIPTION_THREADS` | No | CPU threads available to the transcription model; defaults to `4` |

`FIREBASE_API_KEY` is browser configuration and is not treated as a server secret. The service-account JSON is sensitive and must never be committed, placed in the web root or included in a container image.

## Browser audio capture and local transcription

The **Capture → Record audio** control records in a browser-supported Opus format, uploads the recording through the authenticated application API, and transcribes it on the Index Inbox server with [faster-whisper](https://github.com/SYSTRAN/faster-whisper). The generated text is shown in the capture form for editing before the note is saved. The recording is stored alongside the note when you press **Save**.

The default `tiny.en` model is intended for short English notes and runs on a CPU using INT8 inference. No GPU is required. The first transcription downloads the model into `INDEX_DATA_PATH/models`; later transcriptions reuse that local copy. Choose `base.en` or `small.en` with `TRANSCRIPTION_MODEL` for potentially better accuracy at the cost of more memory and slower CPU transcription.

```dotenv
TRANSCRIPTION_ENABLED=true
TRANSCRIPTION_MODEL=tiny.en
TRANSCRIPTION_LANGUAGE=en
TRANSCRIPTION_THREADS=4
```

Microphone access requires HTTPS or a browser-trusted localhost origin. After deploying an update, allow microphone permission for the Index Inbox hostname. On hardened mobile operating systems, both the browser app and the website must have microphone access. If transcription fails, the recorded audio can still be saved; Index Inbox retries transcription server-side when an audio-only capture is submitted.

Once the model has been downloaded, audio transcription occurs inside the Index Inbox container. The initial model download contacts the model host, but recordings are not sent there.

## Android audio capture widget

After installing the Android app, sign in and grant microphone access from **Capture → Record audio**. Then add the **Index Inbox** widget from the Android home-screen widget picker.

- Tap once to start recording. Android shows a foreground recording notification with Stop and Cancel actions.
- Tap again to stop. Recordings have a five-minute safety limit.
- **Instant save** uploads the audio immediately; the self-hosted server transcribes and saves it as a note.
- **Review first** opens the editable transcript in the app before anything is committed.
- If the server is temporarily unreachable, Instant-save recordings are retained locally and use the existing pending-capture retry queue.

Choose **Instant save** or **Review first** under **Storage & backup → Home-screen audio widget** in the Android app.
The widget uses the same **Light**, **Dark**, or **Follow OS** appearance selected in the app.
Its default capture category can be set to **Note**, **Task**, **Idea**, or **Question** in the same settings section.

Android new-note notifications include the saved note content when available and provide **Archive**, **Star**, **Processed**, and **Delete** quick actions. APK updates show live download progress before Android opens the installer.
Native captures carry a stable idempotency key, so reconnecting after an ambiguous upload response cannot create a second note from the same recording.

## Local authentication setup

Local mode keeps account credentials and sessions in the same SQLite database as Index Inbox. It does not load Firebase or contact Google. There is no public registration or web-based password reset.

1. Generate independent secrets for the webhook and first-run setup:

   ```bash
   openssl rand -hex 32
   openssl rand -hex 32
   ```

2. Create `.env` beside `compose.yaml`. A local-plus-Cloudflare example is:

   ```dotenv
   INDEX_DATA_PATH=/absolute/host/path/to/index-inbox/data
   WEBHOOK_SECRET=first-generated-value
   AUTH_PROVIDER=local
   AUTH_ALLOWED_ORIGINS=https://index.example.com,http://192.168.1.10:5050
   AUTH_COOKIE_SECURE=true
   LOCAL_SETUP_TOKEN=second-generated-value
   ```

   Origins are matched exactly. Use the address shown by `location.origin` in the browser, including the scheme and any non-default port.

3. Pull and start the published container:

   ```bash
   docker compose pull
   docker compose up -d
   ```

   Put the generated value in `LOCAL_SETUP_TOKEN`, open Index Inbox, and use it on the first-run screen to create the first account. Web setup is permanently unavailable after the first local user exists. Remove `LOCAL_SETUP_TOKEN` from `.env` afterward and recreate the container with `docker compose up -d --force-recreate`.

   Alternatively, leave `LOCAL_SETUP_TOKEN` empty and create the first account interactively with `docker exec -it index-inbox flask auth create-user`. Passwords must be at least 12 characters and are hashed with Argon2id. Passwords are never supplied through environment variables or command arguments.

4. To change a password or invalidate signed-in devices:

   ```bash
   docker exec -it index-inbox flask auth change-password
   docker exec -it index-inbox flask auth revoke-sessions
   docker exec index-inbox flask auth list-users
   docker exec -it index-inbox flask auth disable-user
   ```

Changing a password revokes every session for that account. Local login is limited after repeated failures. The browser uses a Secure, HttpOnly, SameSite cookie plus a separate CSRF token for changes.

## Native Android app

The `androidApp` module is a native Kotlin and Jetpack Compose client for local-auth installations. It uses a revocable per-device bearer token; the website continues to use its existing cookie and CSRF authentication.

Deploy the current server before opening the Android app. The SQLite migration for native device tokens runs automatically at startup:

```bash
docker compose pull
docker compose up -d
```

Build a debug APK with Android SDK Platform 35 and JDK 17:

```bash
JAVA_HOME=/path/to/jdk17 ./gradlew :androidApp:assembleDebug
```

The APK is written to `androidApp/build/outputs/apk/debug/androidApp-debug.apk`. On first launch, enter the public HTTPS address of Index Inbox and the existing local username and password. The app stores the returned device token in Android encrypted preferences; it does not retain the password.

Native device tokens expire after `AUTH_DEVICE_DAYS` (90 days by default). Changing the account password or running `flask auth revoke-sessions` immediately revokes both browser sessions and native device tokens.

### Self-hosted Android updates

The published GHCR image embeds the matching signed APK and its version metadata. Signed-in users can download the initial APK using the **Android app** button in the web header or from the matching GitHub Release. Native clients can check, securely download, checksum-verify and open later releases in Android's package installer from **Storage, backups & settings**.

Server owners update the container first with `docker compose pull && docker compose up -d`; the newly embedded APK then becomes available to installed clients. Every Android release is signed with the same permanent key.

Release signing is read from an ignored `keystore.properties` file in the project root:

```properties
storeFile=/absolute/path/to/index-inbox-release.jks
storePassword=your-keystore-password
keyAlias=index-inbox
keyPassword=your-key-password
```

Build the signed artifact with `./gradlew :androidApp:assembleRelease`. Back up the keystore and its passwords: Android will not accept future updates signed with a replacement key.

The Android app records AAC audio notes and uploads a temporary copy to `/api/transcribe` as soon as recording stops. The editable transcription is shown before the user commits the note. Saving uploads the reviewed text and original recording together to `/api/manual`.

For fully self-hosted instant notifications, the signed-in app runs a visible foreground messaging service and holds an authenticated long-poll request to `/api/changes/wait`. Android displays a permanent **Index Inbox connected** notification while this connection is active. New capture and failure events normally arrive within a second of being written by the server. WorkManager polling remains as a battery-managed fallback if Android interrupts the live service.

The live connection occupies one Gunicorn thread per connected Android device. The supplied Docker image runs four threads, which is appropriate for a single-user installation with one or two phones. Increase the Gunicorn thread count before connecting more devices.

Native parity includes active, completed, incomplete, unprocessed, starred, and archived filters; multi-select bulk actions; Item categories; Recent Activity; Collection creation and timelines; archive/reopen controls; and conservative Collection-suggestion review. The server remains the source of truth and the Room cache is refreshed after native mutations.

Native synchronization retrieves every API page and atomically reconciles the Room cache to the complete server snapshot. Notes deleted through the PWA are therefore removed from Android on the next refresh or live change event. Native deletion treats a server `404` as an already-completed delete and removes any stale local copy.

The native administration surface reports Item, audio and database storage, transcription status, and the latest verified backup. It can create a new verified backup and run confirmed age-based audio retention. Collection controls support create, rename, archive/reopen, timeline browsing, suggestion review, and spoken-alias addition/removal.

Inbox parity includes independent state, category, and Collection filters plus assignment between standalone Items and active Collections. Manual capture supports category selection and local recording playback before save. Collection Items can be completed independently of reminders and short actionable Items use a compact checklist presentation. Collection timelines can edit completion, transcription, tags, and category while playing authenticated audio at 0.75×, 1×, 1.5×, or 2× speed. Item, bulk-Item, and Collection removal use explicit confirmation.

Authenticated downloads stream through the native Android document picker, keeping device credentials out of browser URLs and requiring no broad storage permission. The app can save complete JSON, Markdown, and ZIP/audio exports, group-scoped exports, the latest verified backup, and individual original audio files. Configured external backup hooks can also be triggered from the storage screen.

Native settings can disable all activity notifications or independently stop the permanent self-hosted instant connection while retaining periodic fallback sync. The current native device can inspect privacy-safe session metadata and revoke every other device token. Entry details can display the formatted original webhook payload for diagnostics.

Notification settings also control note previews, sound, vibration, and configurable quiet hours. Quiet-hour notifications are delivered silently rather than discarded. The home-screen audio widget supports configurable 1, 3, 5, 10, or 15 second recording limits.

Natural-language captures can create reminders without a cloud service. The layered parser extracts the reminder intent, action and most-specific time expression independently, so the time may occur anywhere in the sentence. Supported forms include relative and compound durations (`in half an hour`, `in two hours and thirty minutes`), bare times, today/tomorrow with morning/afternoon/evening/night, weekdays, named or ISO dates, `this weekend`, and `next week`. Times may use a colon or full stop. Examples include `Remind me at 7.30 to have a coffee`, `Don't forget in a couple of weeks to renew the filter`, and `Remind me next Monday at 9am to submit expenses`.

A bare time means its next occurrence. `REMINDER_CLOCK_FORMAT=24` rolls a passed bare hour to tomorrow; `12` first tries the corresponding PM time, matching a 12-hour phone locale. Calendar expressions use `REMINDER_TIMEZONE`. Relative reminders are anchored to the note's original recording timestamp, so offline/widget retries do not shift their deadlines. Unsupported recurrence and explicit past times remain ordinary notes rather than being guessed.

An early notification can be requested naturally (`with one hour notice`) or edited as minutes in the web/native entry details. The early and due notifications are persisted independently, and completing, deleting, archiving, rescheduling or snoozing reconciles both.

The web and Android inboxes include **Today** and **Reminders** views. Reminder times and early alerts can be changed or removed from entry details and reminders can be marked complete. Android uses exact alarms where the **Alarms & reminders** permission is enabled, restores them after reboot/app update, and automatically retains WorkManager as a battery-managed fallback. Reminder notifications follow the existing privacy, sound, vibration and quiet-hours settings and offer **Complete** and **Snooze 10 min** actions. The server remains the source of truth, so changes made by either client are reconciled on the next sync.

The native parity audit covers inbox search and state/category/group filters, group and alias administration, activity, exports, verified backups, audio retention, and device-session management. Server-only command-line recovery and deployment controls intentionally remain outside the Android client.

Text and audio captures that fail because the network is unavailable or the server returns a 5xx response are stored durably in the Room pending queue. Audio is copied to app-private storage before the capture screen closes. WorkManager retries with network constraints, stable recorded timestamps make retries idempotent, and the Pending screen supports editing metadata or discarding queued text and audio. Client errors such as invalid authentication remain visible instead of being silently queued.

`AUTH_ALLOWED_ORIGINS` accepts multiple exact origins separated by commas, for example a Cloudflare Tunnel URL and a LAN address. Include the scheme and non-default port, and omit paths and trailing slashes.

Secure cookies require HTTPS. If the Cloudflare URL uses HTTPS but the LAN address uses plain HTTP, `AUTH_COOKIE_SECURE=true` protects the remote session but the browser will not authenticate over the LAN HTTP address. Prefer HTTPS on both routes. Use `AUTH_COOKIE_SECURE=false` only for isolated HTTP testing; it permits the local-auth cookie to travel without transport encryption.

### After first-run setup

Once the first account works, harden the production configuration:

1. Remove `LOCAL_SETUP_TOKEN` from `.env`. The browser setup endpoint is already disabled after the first user exists, but the bootstrap secret is no longer needed.
2. Set `AUTH_COOKIE_SECURE=true` when accessing Index Inbox through HTTPS.
3. Remove unused LAN or HTTP entries from `AUTH_ALLOWED_ORIGINS`; retain only the exact origins you use.
4. Do not forward port `5050` from the router. Expose remote access through the HTTPS tunnel or reverse proxy only.

A Cloudflare-only example is:

```dotenv
AUTH_PROVIDER=local
AUTH_COOKIE_SECURE=true
AUTH_ALLOWED_ORIGINS=https://index.example.com
```

### Trusted proxy client addresses

By default, Index Inbox ignores `CF-Connecting-IP` and `X-Forwarded-For`. Login throttling therefore uses the direct network peer, which is safe but may treat every Cloudflare Tunnel visitor as the same address.

**This configuration is optional.** Most single-user installations should leave both variables absent. Docker Compose supplies `TRUSTED_PROXY_HOPS=0`, so no `.env` change is required and forwarding headers remain safely disabled.

Only enable proxy trust when the application port can be reached exclusively through a known reverse proxy or tunnel peer. Configure both the number of forwarding hops and the narrowest peer address or network that contains that proxy:

```dotenv
TRUSTED_PROXY_HOPS=1
TRUSTED_PROXY_CIDRS=172.18.0.4/32
```

With one trusted hop, Index Inbox prefers Cloudflare's `CF-Connecting-IP` value and otherwise uses the rightmost `X-Forwarded-For` address. With multiple hops it selects the configured position from the right of `X-Forwarded-For`. Forwarding headers are ignored whenever the direct peer is outside `TRUSTED_PROXY_CIDRS`, malformed, or shorter than the configured chain.

To identify the direct peer safely, leave proxy trust disabled, make one deliberate failed login through the tunnel, and inspect the most recent attempt:

```bash
docker exec index-inbox flask auth list-attempts
```

Each record retains both `client=` (the address used for throttling) and `peer=` (the direct connection). After configuring trust and recreating the container, repeat the check: `client=` should show the public visitor address while `peer=` should still show the trusted tunnel or proxy address. Do not trust a broad private network merely for convenience; any client able to connect from that network could otherwise supply a forged forwarding header.

Apply environment-only changes without rebuilding the image:

```bash
docker compose up -d --force-recreate
```

Verify the active container configuration:

```bash
docker exec index-inbox printenv AUTH_PROVIDER AUTH_COOKIE_SECURE AUTH_ALLOWED_ORIGINS
```

## Firebase setup

1. Create or select a Firebase project.
2. Open **Authentication → Sign-in method** and enable Email/Password.
3. Create the account that will access Index Inbox.
4. Add the public Index Inbox hostname under **Authentication → Settings → Authorized domains**.
5. Under **Project settings → General**, create a Web app and copy its API key, auth domain and project ID into `.env`.
6. Under **Project settings → Service accounts**, generate a private key and store the JSON file at `FIREBASE_CREDENTIALS_PATH`.
7. Ensure container UID `1000` can read the credentials file and write to `INDEX_DATA_PATH`.

Keep `ALLOWED_EMAILS` populated. Firebase's public client API can create accounts even though Index Inbox does not expose a registration screen; the allowlist is the application authorization boundary.

Set `AUTH_PROVIDER=firebase`. For backward compatibility, installations that do not define `AUTH_PROVIDER` also use Firebase. Only the selected authentication provider is initialized; local mode does not load Firebase browser scripts.

To administratively mark an existing Firebase account verified from the running container:

```bash
docker exec index-inbox python -c "import app; u=app.auth.get_user_by_email('you@example.com'); app.auth.update_user(u.uid,email_verified=True); print('verified')"
```

Sign out and back in afterward so Firebase issues a fresh ID token.

## Pebble webhook setup

Signed-in users can open **Index Ring** in the web app, or **Storage, backups &
settings** in the Android app, to copy the webhook URL and reveal the current
secret. Local authentication requires the current account password before
revealing or rotating it.

Rotating the secret invalidates the previous value immediately. The replacement
is stored in the persistent Index Inbox database, survives container updates and
takes precedence over `WEBHOOK_SECRET` in `.env`. Update the Pebble app as soon as
the rotation completes.

In the Pebble mobile app, create an Index webhook using:

```text
URL: https://index.example.com/webhook/index
Header name: X-Webhook-Secret
Header value: the value of WEBHOOK_SECRET
```

Sending the secret in a header is preferred because query parameters may be recorded in proxy access logs. If the client cannot set headers, Index Inbox also accepts `?token=WEBHOOK_SECRET`.

Test text ingestion:

```bash
curl -X POST 'https://index.example.com/webhook/index' \
  -H 'X-Webhook-Secret: YOUR_WEBHOOK_SECRET' \
  -H 'Content-Type: application/json' \
  -d '{"transcription":"Test the private Index inbox","recordedAt":"1784409957261","client":"ring"}'
```

Test multipart audio ingestion:

```bash
curl -X POST 'https://index.example.com/webhook/index' \
  -H 'X-Webhook-Secret: YOUR_WEBHOOK_SECRET' \
  -F 'transcription=Audio test' \
  -F 'audio=@sample.wav'
```

## Voice categories

Start a ring recording with a category word. The prefix may be followed by ordinary whitespace, a colon, comma, full stop or dash. It is removed from the displayed transcription while the untouched webhook payload remains available for inspection.

```text
Idea: build a Dreamcast inventory app
Task order more resin
Todo, test the webhook tomorrow
Reminder. Call the dentist
Question: how long does the battery last
Note: the blue filament worked best
```

`Todo`, `to-do` and `reminder` map to `task`. Recordings without a recognized prefix remain `note`.

## Voice Collections

Collections combine related Items in the inbox without combining or overwriting their underlying records. Every Item retains its own timestamp, audio and original webhook payload.

### Create and use a Collection

Create a Collection from either client, or with a recording containing only the command:

```text
Create Project42
```

Afterward, begin a recording with that Collection name:

```text
PROJECT42 first site observation
PROJECT42 second site observation
```

The more conversational explicit form also works:

```text
Add to PROJECT42: follow-up observation
```

Collection matching is case-insensitive and only occurs at the beginning of a capture, or after the explicit `Add to` phrase. A sentence such as `Ask whether PROJECT42 is complete` therefore remains a standalone Item.

### Spoken numbers and aliases

Speech transcription may represent the same identifier in different ways. Index Inbox canonicalizes the group name and stores aliases when the group is created:

| Spoken creation command | Displayed group | Accepted capture prefixes |
| --- | --- | --- |
| `Create Project four two` | `PROJECT42` | `Project42`, `Project four two`, `Project forty two` |

Group names may contain letters, numbers, hyphens and underscores, must be 1–32 canonical characters, and are displayed in uppercase.

### Display and automatic updates

Each addition remains an independent stored entry with its original timestamp, audio and webhook payload, while the inbox presents entries from the same group together. Use the group filter to focus on one group. New webhook captures and groups appear automatically within about five seconds; automatic refresh pauses while a note is being edited or a dialog is open.

The browser shows a dismissible notice when it receives a standalone Item, adds an Item to a Collection, creates a Collection, sees a repeated create command, cannot recognize a create command, rejects a webhook, or fails to store a capture. Notices are deduplicated by activity ID and disappear after ten seconds. They contain only a generic result and, where relevant, the canonical Collection name—never the Item transcription or original payload.

### Remove a Collection

Use **Collections** in the web interface to create, rename, archive, reopen, or remove Collections. Removing a Collection never deletes its Items or audio; existing additions become standalone Items after confirmation.

The group manager also supports:

- **Timeline** — opens the complete group history from oldest to newest, including editable transcription, tags, category, and any stored audio. Pending edits finish saving before the timeline closes, then the main inbox reloads to show them.
- **Rename** — updates every assigned entry atomically and retains the old name as a spoken alias.
- **Archive** — closes the group so new voice captures no longer match it while preserving its entries and aliases.
- **Reopen** — makes an archived group available for voice matching and manual assignment again.
- **Aliases** — lists the phrases that match a group and allows additional spoken forms to be added or removed. Canonical group-name aliases cannot be removed, and aliases cannot be shared by different groups.

Each note card includes a group selector. Choose an active group to assign or move the note, or choose **Standalone** to remove it from its current group. Archived groups remain visible on entries already assigned to them but cannot receive new manual assignments until reopened.

### Per-Collection exports

Open **Manage groups**, select **Timeline** for a group, and choose one of its export controls:

- **Markdown** creates a chronological readable document containing the group name, timestamps, categories, tags, and transcriptions.
- **JSON** preserves the complete stored entry records for that group, including their original payload metadata.
- **ZIP + audio** contains both formats plus every available audio file assigned to that group.

Exports are scoped to the selected group. Empty and archived groups remain available, and exporting an empty group produces valid empty Markdown, JSON, or ZIP output.

### Suggested grouping

Index Inbox can suggest a Collection when a standalone Item begins with a slightly misheard or mistyped Collection identifier. Open **Collections** and use **Review suggestions** to inspect them.

Suggestions are deliberately conservative:

- The identifier must appear at the beginning of the note.
- Its numeric portion must exactly match an active group.
- Only a small difference in the name portion is allowed.
- Archived groups are never suggested.

For example, if `SITELOG42` exists, a standalone Item beginning `SITLOG42` may be suggested for it, while `SITLOG43` will not be. Nothing moves automatically. **Accept** assigns the Item and removes the proposed identifier from its transcription; **Dismiss** permanently hides that Item/Collection suggestion. Neither action creates or learns a spoken alias.

Server administrators can also inspect groups or remove an incorrectly transcribed empty group:

```bash
docker exec index-inbox flask groups list
docker exec -it index-inbox flask groups delete-empty
```

## Data and backups

The server's backward-compatible Item model and Collection API aliases are documented in [`docs/phase-2-server-model.md`](docs/phase-2-server-model.md). General Item completion, inbox processing, and reminder completion are separate states. Existing Entry/Group API routes remain supported during the migration.

`INDEX_DATA_PATH` contains:

```text
index-inbox.sqlite3
audio/
backups/
```

Use **Storage, backup & export → Create verified backup** to create a consistent SQLite snapshot plus all referenced audio. The resulting ZIP is stored under `backups/`, includes a SHA-256 manifest, and can be downloaded from the same screen. Index Inbox retains the five newest local archives. Creation time, outcome, archive size, and errors are recorded in the database and Recent activity.

Verify any archive without changing production data:

```bash
docker exec index-inbox flask backup verify /data/backups/ARCHIVE_NAME.zip
```

The verifier checks every file against the manifest, rejects missing or unexpected content, runs SQLite's integrity check against a temporary extraction, and confirms the entry/audio counts. A successful check does not prove that a separate off-server copy exists, so copy verified archives to another machine or backup target.

Backup archives contain the complete database, including local account password hashes and session records, plus note payloads and audio. Treat them as sensitive and protect off-server copies with appropriate access controls and encryption.

### Safe staging restore check

Never test a restore over the production directory. On Unraid, use a new empty staging directory and bind the restored data to a disposable container on a different localhost-only port:

```bash
mkdir -p /mnt/user/appdata/index-restore-test
unzip -q /mnt/user/appdata/index-local-login/data/backups/ARCHIVE_NAME.zip \
  -d /mnt/user/appdata/index-restore-test

RESTORE_IMAGE=$(docker inspect index-inbox --format '{{.Config.Image}}')
docker run -d --name index-inbox-restore-check \
  -p 127.0.0.1:5051:8080 \
  --env-file /mnt/user/appdata/index-local-login/.env \
  -e DATA_DIR=/data \
  -v /mnt/user/appdata/index-restore-test:/data \
  "$RESTORE_IMAGE"

curl http://127.0.0.1:5051/health
docker stop index-inbox-restore-check
docker rm index-inbox-restore-check
```

The health request must return `{"ok":true}`. The staging container uses the restored database and audio only; it never mounts production `/data`. Use a fresh staging directory for each restore test.

For a conventional file-level backup of live `/data`, remember that SQLite WAL mode is enabled: use a filesystem snapshot or briefly stop the container for consistency. The web interface can also export JSON, Markdown, or a non-restorable content ZIP.

`BACKUP_HOOK_URL` can point to n8n or another automation endpoint. Index Inbox sends a small JSON event when the backup control is triggered; the receiving workflow is responsible for performing the backup.

## Updating

Index Inbox performs additive SQLite migrations automatically. Preserve `INDEX_DATA_PATH`, pull the published image and recreate the service:

```bash
docker compose pull
docker compose up -d
```

Existing accounts, entries and audio remain intact. Pin `INDEX_INBOX_VERSION` in `.env` when you prefer controlled upgrades instead of `latest`.

## Publishing releases

Pushing a tag such as `v0.10.1` runs `.github/workflows/release.yml`. GitHub Actions tests and signs the Android app, embeds that exact APK into the server image, publishes versioned and `latest` tags to GHCR, and creates a GitHub Release containing the APK, Compose file, environment template and setup helper.

The repository must define these encrypted Actions secrets:

- `ANDROID_KEYSTORE_BASE64`
- `ANDROID_STORE_PASSWORD`
- `ANDROID_KEY_PASSWORD`

The signing files and passwords are ignored by Git and Docker. Never replace the signing key after users install a release; Android will reject updates signed by another identity.

## Development tests

Run the Python API and security regression suite with:

```bash
python -m unittest discover -s tests -v
```

The Playwright suite starts its own temporary local-auth server under the system temporary directory. It never reads or modifies the configured production `INDEX_DATA_PATH`:

```bash
npm ci
npx playwright install chromium
npm run test:e2e
```

The browser flow covers first-run setup, subsequent login, live webhook refresh, note groups, suggestion acceptance, rename/archive/reopen behavior, timeline saving, group downloads, and a narrow mobile viewport. Pull requests and pushes to `main` run the Python server, Android unit, and Chromium suites in GitHub Actions; failed browser runs upload the Playwright HTML report, traces, and screenshots for seven days.





## Troubleshooting

Check container health and logs:

```bash
curl http://127.0.0.1:5050/health
docker logs --tail 100 index-inbox
```

If startup reports `Permission denied: '/data/audio'`, make `INDEX_DATA_PATH` writable by UID `1000`.

If Compose reports `invalid spec: :/data`, ensure `.env` is beside `compose.yaml` and contains an absolute `INDEX_DATA_PATH`. Confirm what Compose loaded with:

```bash
docker compose config --environment | grep INDEX_DATA_PATH
```

If local setup unexpectedly shows Firebase login, verify both Compose and the running container:

```bash
docker compose config --environment | grep AUTH_PROVIDER
docker exec index-inbox printenv AUTH_PROVIDER AUTH_ALLOWED_ORIGINS
curl -i http://127.0.0.1:5050/auth/session
```

An empty local installation returns `401` with `setupRequired: true`. If setup reports `Invalid request origin`, compare `AUTH_ALLOWED_ORIGINS` with the exact address shown by `location.origin`; a hostname, IP address, scheme or port difference represents a different origin.

Rebuilding or recreating a container does not remove accounts or sessions from `INDEX_DATA_PATH`. Use `flask auth revoke-sessions` to log out existing devices, or point `INDEX_DATA_PATH` at a new empty directory when testing the complete first-run flow. Stop the container before manually moving SQLite files.

If the web interface appears stale after an update, confirm the version shown in its header, close all open tabs and clear the site's cached data once. Index Inbox uses a service worker for PWA and offline support.

## Security notes

- Use HTTPS for all public access.
- Keep the webhook secret in a custom header.
- Keep the Firebase email allowlist enabled when using Firebase mode.
- Keep `AUTH_COOKIE_SECURE=true` and configure exact `AUTH_ALLOWED_ORIGINS` in local mode.
- Never commit `.env` or service-account JSON files.
- Restrict filesystem access to the persistent data and credentials paths.
- Disable cloud transcription or backup in the Pebble app if an entirely local processing path is required.
- Rotate credentials immediately if they are accidentally disclosed.
