# Phase 4 deterministic interpretation contract

Index Inbox uses one server-side `interpret_capture()` function to classify natural-language captures. Version `1.0` supports these operations:

- `create_item`
- `create_collection`
- `add_to_collection`
- `set_reminder`
- `complete_item`
- `search_items`

Every result has the same shape:

```json
{
  "version": "1.0",
  "operation": "set_reminder",
  "arguments": {
    "text": "call Mum",
    "dueAt": "2026-08-03T08:00:00+00:00",
    "notifyBeforeMinutes": null
  },
  "confidence": 0.99,
  "explanation": "Create an Item with the requested reminder time.",
  "ambiguous": false,
  "requiresConfirmation": false
}
```

`confidence` is a deterministic parser score, not a statistical model probability. `explanation` records why the operation was selected. `ambiguous` means the arguments cannot identify one safe operation. `requiresConfirmation` means a client must obtain an explicit user decision before execution even when the match is unique.

## Dry-run API

Authenticated clients can call `POST /api/interpret`:

```json
{
  "text": "Remind me tomorrow at 9am to call Mum",
  "referenceAt": "2026-08-02T12:00:00+00:00",
  "collectionName": "OPTIONAL_COLLECTION"
}
```

The endpoint only returns an interpretation. It does not create, update, complete, search, log, or otherwise mutate stored data. `referenceAt` is optional and makes relative reminder interpretation reproducible. `groupName` remains accepted as a compatibility alias for `collectionName`.

## Safety and execution boundary

Existing unambiguous Collection creation, Collection assignment, and reminder capture behaviour remains backward compatible and is now driven by the shared interpreter. Phase 4 does not automatically execute newly introduced completion or search operations. Completion results always require confirmation; zero or multiple matches are also marked ambiguous. Shared client preview and correction arrive in Phase 5, and automatic execution policy remains Phase 6 work.

No language model or external service is involved. The parser is deterministic, fully self-hosted, and covered by fixtures with fixed reference times.
