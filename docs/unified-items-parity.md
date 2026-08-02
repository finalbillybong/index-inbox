# Unified Items parity and migration plan

Status: Phase 1 baseline. `Current` describes the repository before the Items/Collections migration; `Target` is the shared product contract.

## Feature-parity matrix

| Capability | Server/API current | Responsive web current | Installed PWA current | Android current | Unified target / gate |
| --- | --- | --- | --- | --- | --- |
| Text capture | `/api/manual` and webhook | Capture dialog | Same UI; service-worker shell | Native composer and offline retry | One capture contract and equivalent validation |
| Audio capture | upload, transcription, audio endpoint | record, preview, edit transcription | same, subject to browser microphone support | composer and widget; queued retry | Correctable transcription before commit; idempotent retry |
| Inbox and filters | entry queries | active/today/reminders/archive/unprocessed/starred | same cached shell; live data requires server | native filters | Same state names and empty/error behaviour |
| Item editing | entry PATCH | expanded card | same | detail/editor | Same editable fields and save feedback |
| Star | entry PATCH/bulk | outline/filled state | same | outline/filled state | Equivalent accessible state and action |
| Processed | independent entry field | card and bulk action | same | detail/action | Remains distinct from Completed |
| Reminder | due, early alert, reminder completion | view, shortcuts, editor | same; browser notification limits apply | view, editor, scheduled notifications | Reminder remains an Item view; same lifecycle |
| Collections (Groups today) | group CRUD, aliases, suggestions, timeline | secondary Group screen | same | Group management screens | Rename together; preserve legacy APIs during migration |
| Audio playback/download | authenticated audio endpoint | player, speed, download | same | player/download | Same labelled actions and missing-audio handling |
| Original payload | stored JSON | expanded card | same | detail screen | Read-only, clearly labelled, safe rendering |
| Archive/delete | entry/group APIs | card, bulk, confirmations | same | detail/actions | Same semantics; destructive actions confirmed |
| Search | server text/title/tag query | debounced search | same | native search/filter | Search target and clearing behaviour match |
| Activity/live arrival | changes/activity APIs | polling notices/activity screen | same while online | instant sync notifications | Same event language and deep links |
| Exports/backups | JSON/Markdown/ZIP and verified backup | settings/group actions | same | server-backed settings/actions | Existing formats remain readable; new fields additive |
| Index Ring | authenticated webhook, secret rotation | settings and test capture | same | arrival notifications/deep link | Route through shared interpreter without breaking payloads |
| Offline behaviour | idempotent source keys | shell only | cached shell, queued behaviour not general | Room cache and capture retry | Explicit, tested delivery state; no duplicates |
| Theme/navigation | configuration/state | responsive header/bottom nav | standalone presentation | native theme, back stack, widget | Matching destinations and terminology; platform-native layout |
| General Completed state | not present | not present | not present | not present | Add separately in Phase 2, then expose together |

Installed-PWA verification is separate from responsive-browser verification. Its gate includes service-worker upgrade, standalone display, cached-shell startup, reauthentication behaviour, and recovery after reconnect.

## Non-destructive server migration requirements

Every schema migration must:

1. run inside an explicit transaction and be safe to retry after interruption;
2. make an external verified backup available before a destructive or table-rebuilding step;
3. preserve every entry ID, source key, creation/recording timestamp, title, transcription, category, tag, star, Processed state, archive state, Group assignment, due time, reminder completion, early-alert value, payload, audio path, and MIME type;
4. preserve Group canonical names, display names, aliases, archive state, suggestions, and chronology;
5. use additive nullable/defaulted fields before any semantic cutover;
6. retain legacy Group endpoints and legacy JSON fields for at least one published compatibility cycle after Collection vocabulary is introduced;
7. make new response fields optional/defaultable so an older Android client can still decode responses;
8. keep exports and backups capable of representing all old fields and any new completion/Collection fields;
9. record a schema version and migration outcome that support diagnosis without exposing content;
10. validate row counts, stable identifiers, Group membership, reminder values, and referenced audio files before committing.

## Rollback requirements

- A pre-migration backup is the authoritative rollback boundary; application downgrade alone is not a database rollback.
- During the compatibility window, old server and Android clients must ignore additive fields and continue using legacy endpoints.
- A rollback procedure must stop writers, retain the failed database for diagnosis, restore the verified database plus audio as one unit, start the prior image, and run health and representative read checks.
- No rollback may delete captures accepted after the backup without an explicit export/replay decision.
- Removal or repurposing of a legacy column/endpoint requires a later reviewed phase, a documented minimum supported version, and a restore rehearsal.

## Compatibility fixtures that must stay green

- A legacy standalone text entry retains identity, content, state, and payload.
- A Group and its aliases retain membership and chronological export scope.
- A reminder retains due time, early alert, reminder completion, and Processed independently.
- Audio remains downloadable and included byte-for-byte in ZIP export and verified backup.
- Repeated manual/widget delivery with the same source key creates one Item.
- Index Ring webhook capture retains its original payload and expected live-arrival event.
- Android decodes server entries whose optional fields are absent and applies defaults.

These fixtures are compatibility locks, not the full feature test suite. Phase 2 must add migration tests against a representative pre-migration SQLite database before changing the schema.
