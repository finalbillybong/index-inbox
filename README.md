# Index Inbox

Index Inbox is a private, self-hosted capture and organization service for Pebble Index 01 recordings. The Pebble mobile app transcribes a recording and sends it to Index Inbox through an authenticated HTTPS webhook. Notes and optional audio remain on storage you control.

## Highlights

- Flexible JSON and multipart webhook ingestion
- Header-based webhook authentication
- Firebase Email/Password authentication with an email allowlist
- SQLite metadata and local audio storage
- Retry deduplication and delivery activity history
- Editable transcriptions, tags, categories, starring and archiving
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

## Requirements

- Docker Engine with Docker Compose
- A public HTTPS hostname through a reverse proxy or secure tunnel
- A Firebase project with Email/Password authentication enabled
- A Firebase service-account JSON file for server-side token verification

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

3. Put the generated value in `WEBHOOK_SECRET` and configure the remaining variables.

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
| `FIREBASE_PROJECT_ID` | Yes | Firebase project identifier |
| `FIREBASE_API_KEY` | Yes | Firebase web application API key |
| `FIREBASE_AUTH_DOMAIN` | Yes | Usually `PROJECT_ID.firebaseapp.com` |
| `ALLOWED_EMAILS` | Recommended | Comma-separated lowercase email allowlist |
| `REQUIRE_VERIFIED_EMAIL` | Recommended | Require Firebase's `email_verified` claim |
| `INDEX_DATA_PATH` | Yes | Persistent host directory for SQLite and audio |
| `FIREBASE_CREDENTIALS_PATH` | Yes | Host path to the service-account JSON file |
| `BACKUP_HOOK_URL` | No | Automation endpoint called from the backup control |

`FIREBASE_API_KEY` is browser configuration and is not treated as a server secret. The service-account JSON is sensitive and must never be committed, placed in the web root or included in a container image.

## Firebase setup

1. Create or select a Firebase project.
2. Open **Authentication → Sign-in method** and enable Email/Password.
3. Create the account that will access Index Inbox.
4. Add the public Index Inbox hostname under **Authentication → Settings → Authorized domains**.
5. Under **Project settings → General**, create a Web app and copy its API key, auth domain and project ID into `.env`.
6. Under **Project settings → Service accounts**, generate a private key and store the JSON file at `FIREBASE_CREDENTIALS_PATH`.
7. Ensure container UID `1000` can read the credentials file and write to `INDEX_DATA_PATH`.

Keep `ALLOWED_EMAILS` populated. Firebase's public client API can create accounts even though Index Inbox does not expose a registration screen; the allowlist is the application authorization boundary.

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
Action turn off the workshop lights
Note: the blue filament worked best
```

`Todo`, `to-do` and `reminder` map to `task`. Recordings without a recognized prefix remain `note`.

## Data and backups

`INDEX_DATA_PATH` contains:

```text
index-inbox.sqlite3
audio/
```

Back up the complete directory. SQLite WAL mode is enabled, so use a filesystem snapshot or briefly stop the container for a consistent file-level backup. The web interface can also export JSON, Markdown or a ZIP containing metadata and audio.

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

If the web interface appears stale after an update, confirm the version shown in its header, close all open tabs and clear the site's cached data once. Index Inbox uses a service worker for PWA and offline support.

## Security notes

- Use HTTPS for all public access.
- Keep the webhook secret in a custom header.
- Keep the Firebase email allowlist enabled.
- Never commit `.env` or service-account JSON files.
- Restrict filesystem access to the persistent data and credentials paths.
- Disable cloud transcription or backup in the Pebble app if an entirely local processing path is required.
- Rotate credentials immediately if they are accidentally disclosed.
