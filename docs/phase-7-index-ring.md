# Phase 7 Index Ring interpretation

Index Ring captures now enter the same deterministic interpretation and safe-execution policy as responsive web, installed PWA, and native Android captures.

## Pebble contract

The current Pebble mobile app posts multipart data containing `recordedAt`, `client=ring`, and optionally `transcription` and AAC/M4A `audio`. The M4A filename contains Pebble's stable recording ID. `X-Index-Trigger` identifies `single-click-hold` or `double-click-hold`.

Index Inbox preserves the recording ID and gesture in the Item's original payload. The recording ID is the idempotency key, so a retry cannot create a second Item even when the audio bytes or transcription differ.

## Operation behavior

- Ordinary speech creates a normal Item and does not produce a redundant operation receipt.
- With capture automation enabled, high-confidence Collection creation, Collection addition, and reminder commands execute through the Phase 6 allowlist.
- A uniquely matched completion command is stored safely with an `awaiting_confirmation` receipt. **Recent activity** offers **Confirm operation** on web/PWA and Android. Confirmation completes the target and archives the command Item; Undo reopens the target and restores the command Item.
- Ambiguous, multiply matched, destructive, unsupported, or low-confidence commands are retained as plain Items. They cannot be confirmed until the user corrects the ambiguity manually.
- When automation is disabled, commands remain plain Items and their receipts explain why no operation ran.

Interpreted Ring outcomes appear in the live change feed, Recent Activity, responsive browser notices, and native Android notifications. Tapping an Android outcome opens the affected Item when one exists, or Recent Activity otherwise.
