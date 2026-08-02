# Phase 2 server model and compatibility

Phase 2 introduces additive Item and Collection vocabulary. It intentionally does not rename the current user interface.

## State model

`completed` is the general Item completion state. It defaults to `0` for every existing and new Item.

- `completed`: the Item or action is finished.
- `processed`: the Item has been reviewed or triaged.
- `reminder_completed`: the scheduled reminder occurrence has been dealt with.

The three fields are independent. Updating one never changes either of the others.

## Compatibility API

The existing `/api/entries` and `/api/groups` routes remain supported. Their stored `group_name` vocabulary is unchanged.

New clients may use:

- `/api/items` and `/api/items/<id>`;
- `/api/items/bulk`, `/api/items/<id>/audio`, and `/api/items/export/<format>`;
- `/api/collections` and the equivalent lifecycle, alias, timeline, and export sub-routes;
- `/api/collection-suggestions` and its accept/dismiss sub-routes.

Item responses from `/api/items` add `collection_name` while retaining `group_name`. Item mutations accept either name; if both are supplied, the legacy `group_name` value wins. Collection names are also searchable. JSON and Markdown exports contain general completion and Collection semantics.

## Upgrade and rollback boundary

Server startup adds `entries.completed INTEGER NOT NULL DEFAULT 0` in the existing migration transaction and advances SQLite `user_version` to 2. No table is rebuilt and no existing column is removed or rewritten.

Before deploying, create and copy a verified backup off-server. The migration is retryable. An older server can read all legacy columns after this additive migration, but it cannot understand changes made only through the new completion field. Therefore application downgrade is suitable for emergency read compatibility; restoring the verified pre-upgrade database and its audio directory is the authoritative full rollback.

Backups retain manifest version 1 for older-verifier compatibility and add `schemaVersion`, `completedItems`, and `collections`. Verification checks those values when present while continuing to accept older manifests.
