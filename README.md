# Index Inbox

Index Inbox is a private, self-hosted capture and organization service for Pebble Index 01 recordings. The Pebble mobile app transcribes a recording and sends it to Index Inbox through an authenticated HTTPS webhook. Notes and optional audio remain on storage you control.

## Highlights

- Flexible JSON and multipart webhook ingestion
- Header-based webhook authentication
- Choice of fully local authentication or Firebase Email/Password authentication
- SQLite metadata and local audio storage
- Retry deduplication and delivery activity history
- Editable transcriptions, tags, categories, starring and archiving
- Explicit voice-created note groups with combined presentation
- Chronological group timelines with editing, audio, and group-scoped exports
- Conservative, user-confirmed suggestions for near-matching group identifiers
- Automatic background refresh with live, dismissible capture notices
- Search, filters, pagination and bulk actions
- Original webhook payload inspection
- Audio playback, speed controls, downloads and retention cleanup
- JSON, Markdown and ZIP/audio exports
- Optional external backup hook
- Installable responsive PWA with manual text/audio capture
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

## Requirements

- Docker Engine with Docker Compose
- A public HTTPS hostname through a reverse proxy or secure tunnel
- For Firebase mode only: a Firebase project and service-account JSON file

## Quick start

1. Clone the repository:

   ```bash
   git clone https://github.com/finalbillybong/index-inbox.git
   cd index-inbox
   ```

2. Create the environment file:

   ```bash
   cp .env.example .env
   openssl rand -hex 32
   ```

3. Put the generated value in `WEBHOOK_SECRET`, choose `AUTH_PROVIDER=local` or `AUTH_PROVIDER=firebase`, and configure the remaining variables.

4. Start the service:

   ```bash
   docker compose up -d --build
   ```

5. Check its health:

   ```bash
   curl http://127.0.0.1:5050/health
   ```

   A healthy service responds with `{"ok":true}`.

The host exposes port `5050`; the container listens on port `8080`. Point a reverse proxy or secure tunnel at `http://SERVER_ADDRESS:5050` and terminate HTTPS before exposing the application publicly. Do not forward port `5050` directly from a router.

## Configuration

| Variable | Required | Description |
| --- | --- | --- |
| `WEBHOOK_SECRET` | Yes | Random secret used to authenticate incoming Index webhooks |
| `AUTH_PROVIDER` | Yes | `local` for self-hosted accounts or `firebase` for Firebase Authentication |
| `AUTH_ALLOWED_ORIGINS` | Local | Comma-separated allowed browser origins, including scheme and port |
| `AUTH_EXPECTED_ORIGIN` | Local | Deprecated single-origin setting retained for compatibility |
| `AUTH_COOKIE_SECURE` | Local | Keep `true` in production; set `false` only for localhost HTTP testing |
| `AUTH_SESSION_DAYS` | Local | Absolute local-session lifetime, default 30 days |
| `AUTH_IDLE_DAYS` | Local | Local-session idle timeout, default 7 days |
| `TRUSTED_PROXY_HOPS` | No | Number of trusted forwarding hops; defaults to `0`, which ignores forwarded client-IP headers |
| `TRUSTED_PROXY_CIDRS` | Proxy trust | Comma-separated IP networks allowed to supply forwarded client addresses |
| `LOCAL_SETUP_TOKEN` | Local setup | One-time secret required to create the first owner through the browser |
| `FIREBASE_PROJECT_ID` | Firebase | Firebase project identifier |
| `FIREBASE_API_KEY` | Firebase | Firebase web application API key |
| `FIREBASE_AUTH_DOMAIN` | Firebase | Usually `PROJECT_ID.firebaseapp.com` |
| `ALLOWED_EMAILS` | Recommended | Comma-separated lowercase email allowlist |
| `REQUIRE_VERIFIED_EMAIL` | Recommended | Require Firebase's `email_verified` claim |
| `INDEX_DATA_PATH` | Yes | Persistent host directory for SQLite and audio |
| `FIREBASE_CREDENTIALS_PATH` | Firebase | Host path to the service-account JSON file |
| `BACKUP_HOOK_URL` | No | Automation endpoint called from the backup control |

`FIREBASE_API_KEY` is browser configuration and is not treated as a server secret. The service-account JSON is sensitive and must never be committed, placed in the web root or included in a container image.

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

3. Build and start the container:

   ```bash
   docker compose up -d --build
   ```

   Put the generated value in `LOCAL_SETUP_TOKEN`, open Index Inbox, and use it on the first-run screen to create the owner account. Web setup is permanently unavailable after the first local user exists. Remove `LOCAL_SETUP_TOKEN` from `.env` afterward and recreate the container with `docker compose up -d --force-recreate`.

   Alternatively, leave `LOCAL_SETUP_TOKEN` empty and create the first account interactively with `docker exec -it index-inbox flask auth create-user`. Passwords must be at least 12 characters and are hashed with Argon2id. Passwords are never supplied through environment variables or command arguments.

4. To change a password or invalidate signed-in devices:

   ```bash
   docker exec -it index-inbox flask auth change-password
   docker exec -it index-inbox flask auth revoke-sessions
   docker exec index-inbox flask auth list-users
   docker exec -it index-inbox flask auth disable-user
   ```

Changing a password revokes every session for that account. Local login is limited after repeated failures. The browser uses a Secure, HttpOnly, SameSite cookie plus a separate CSRF token for changes.

`AUTH_ALLOWED_ORIGINS` accepts multiple exact origins separated by commas, for example a Cloudflare Tunnel URL and a LAN address. Include the scheme and non-default port, and omit paths and trailing slashes.

Secure cookies require HTTPS. If the Cloudflare URL uses HTTPS but the LAN address uses plain HTTP, `AUTH_COOKIE_SECURE=true` protects the remote session but the browser will not authenticate over the LAN HTTP address. Prefer HTTPS on both routes. Use `AUTH_COOKIE_SECURE=false` only for isolated HTTP testing; it permits the local-auth cookie to travel without transport encryption.

### After first-run setup

Once the owner account works, harden the production configuration:

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

## Voice note groups

Voice note groups combine related captures in the inbox without combining or overwriting their underlying records. Every addition retains its own timestamp, audio and original webhook payload.

### Create and use a group

Create a group with a recording containing only the command:

```text
Create Project42
```

Afterward, begin a recording with that group name:

```text
PROJECT42 first site observation
PROJECT42 second site observation
```

The more conversational explicit form also works:

```text
Add to PROJECT42: follow-up observation
```

Group matching is case-insensitive and only occurs at the beginning of a capture, or after the explicit `Add to` phrase. A sentence such as `Ask whether PROJECT42 is complete` therefore remains a standalone note.

### Spoken numbers and aliases

Speech transcription may represent the same identifier in different ways. Index Inbox canonicalizes the group name and stores aliases when the group is created:

| Spoken creation command | Displayed group | Accepted capture prefixes |
| --- | --- | --- |
| `Create Project four two` | `PROJECT42` | `Project42`, `Project four two`, `Project forty two` |

Group names may contain letters, numbers, hyphens and underscores, must be 1–32 canonical characters, and are displayed in uppercase.

### Display and automatic updates

Each addition remains an independent stored entry with its original timestamp, audio and webhook payload, while the inbox presents entries from the same group together. Use the group filter to focus on one group. New webhook captures and groups appear automatically within about five seconds; automatic refresh pauses while a note is being edited or a dialog is open.

The browser shows a dismissible notice when it receives a standalone note, adds a note to a group, creates a group, sees a repeated create command, cannot recognize a create command, rejects a webhook, or fails to store a capture. Notices are deduplicated by activity ID and disappear after ten seconds. They contain only a generic result and, where relevant, the canonical group name—never the note transcription or original payload.

### Remove a group

Use **Manage groups** in the web interface to remove empty or populated groups. Removing a group never deletes its entries or audio; existing additions become standalone notes after confirmation.

The group manager also supports:

- **Timeline** — opens the complete group history from oldest to newest, including editable transcription, tags, category, and any stored audio. Pending edits finish saving before the timeline closes, then the main inbox reloads to show them.
- **Rename** — updates every assigned entry atomically and retains the old name as a spoken alias.
- **Archive** — closes the group so new voice captures no longer match it while preserving its entries and aliases.
- **Reopen** — makes an archived group available for voice matching and manual assignment again.
- **Aliases** — lists the phrases that match a group and allows additional spoken forms to be added or removed. Canonical group-name aliases cannot be removed, and aliases cannot be shared by different groups.

Each note card includes a group selector. Choose an active group to assign or move the note, or choose **Standalone** to remove it from its current group. Archived groups remain visible on entries already assigned to them but cannot receive new manual assignments until reopened.

### Per-group exports

Open **Manage groups**, select **Timeline** for a group, and choose one of its export controls:

- **Markdown** creates a chronological readable document containing the group name, timestamps, categories, tags, and transcriptions.
- **JSON** preserves the complete stored entry records for that group, including their original payload metadata.
- **ZIP + audio** contains both formats plus every available audio file assigned to that group.

Exports are scoped to the selected group. Empty and archived groups remain available, and exporting an empty group produces valid empty Markdown, JSON, or ZIP output.

### Suggested grouping

Index Inbox can suggest a group when a standalone note begins with a slightly misheard or mistyped group identifier. Open **Manage groups** and use **Review suggestions** to inspect them.

Suggestions are deliberately conservative:

- The identifier must appear at the beginning of the note.
- Its numeric portion must exactly match an active group.
- Only a small difference in the name portion is allowed.
- Archived groups are never suggested.

For example, if `SITELOG42` exists, a standalone note beginning `SITLOG42` may be suggested for it, while `SITLOG43` will not be. Nothing moves automatically. **Accept** assigns the note and removes the proposed identifier from its transcription; **Dismiss** permanently hides that entry/group suggestion. Neither action creates or learns a spoken alias.

Server administrators can also inspect groups or remove an incorrectly transcribed empty group:

```bash
docker exec index-inbox flask groups list
docker exec -it index-inbox flask groups delete-empty
```

## Data and backups

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

Index Inbox performs additive SQLite migrations automatically. Preserve `INDEX_DATA_PATH`, pull the new code and rebuild:

```bash
git pull
docker compose down
docker compose build --no-cache
docker compose up -d
```

Existing entries and audio remain intact.

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
