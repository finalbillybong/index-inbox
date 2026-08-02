# Unified Items and Collections Roadmap

This document tracks the evolution of Index Inbox from a notes-first application into a unified capture system. It is the source of truth for scope, terminology, cross-client parity, and publication status.

## Tracking rules

- An item remains unchecked while it is planned, being implemented, under review, or merged but not released where a release is required.
- Check an item only after its implementation is merged into `main` and all listed surfaces meet the parity requirement.
- A phase is complete only when every required checkbox in that phase is complete.
- Record the merged pull request beside completed work.
- Record the release tag when work requires a new server image or Android APK.
- Do not silently remove or reinterpret existing entries, groups, reminders, audio, or integrations.

## Product contract

- **Item:** The universal stored object. It can contain a title, text, audio, category, tags, collection, due time, and completion state.
- **Collection:** A named container for related items. This replaces the user-facing term **Group** while retaining backward compatibility internally.
- **Reminder:** An item with a due time, not a separate content type.
- **Completed:** The item or action is finished.
- **Processed:** The item has been reviewed. Processed and Completed must remain separate concepts.
- **Capture:** One universal input that can propose or perform an operation based on the user's natural request.

## Cross-client parity gate

No user-facing capability is complete until all applicable boxes are checked:

- [ ] Server schema and API support
- [ ] Responsive web implementation
- [ ] Installed PWA behaviour
- [ ] Native Android implementation
- [ ] Matching terminology and core actions across clients
- [ ] Upgrade compatibility with existing data
- [ ] Automated server and browser tests
- [ ] Automated Android tests
- [ ] Documentation updated
- [ ] Merged and published where required

The web app and PWA share an implementation, but browser, mobile-responsive, offline/cache, and standalone-installed behaviour must be verified separately.

## Phase 1 — Product contract and compatibility foundation

- [ ] Confirm the Item, Collection, Reminder, Completed, and Processed definitions.
- [ ] Document the shared navigation and item-card behaviour for web/PWA and Android.
- [ ] Create a detailed feature-parity matrix for responsive web, installed PWA, and Android.
- [ ] Define non-destructive database migration and rollback requirements.
- [ ] Add compatibility tests covering existing entries, groups, reminders, audio, exports, and Index Ring captures.
- [ ] Publish Phase 1. PR: _pending_. Release: _not expected unless runtime code changes_.

## Phase 2 — Backward-compatible server model

- [ ] Add a general item completion field without reusing `processed` or `reminder_completed` incorrectly.
- [ ] Preserve all existing entry IDs, group assignments, reminder values, audio paths, and timestamps.
- [ ] Add Collection-compatible API vocabulary while retaining legacy Group endpoints during migration.
- [ ] Include completion and collection semantics in changes, activity, search, exports, backups, and restore paths.
- [ ] Prove migration and rollback behaviour against representative existing databases.
- [ ] Publish Phase 2. PR: _pending_. Release: _pending_.

## Phase 3 — Collections and checklist presentation

- [ ] Rename Groups to Collections in all user-facing web/PWA copy.
- [ ] Rename Groups to Collections in all user-facing Android copy.
- [ ] Update notifications, Recent Activity, exports, setup guidance, and documentation.
- [ ] Allow any item to be completed independently of reminder status.
- [ ] Present short actionable collection items as a checklist without preventing longer notes.
- [ ] Preserve chronological collection timelines and aliases.
- [ ] Provide the same create, rename, archive, complete, reopen, and remove actions on web/PWA and Android.
- [ ] Publish Phase 3. PR: _pending_. Release: _pending_.

## Phase 4 — Central deterministic interpretation contract

- [ ] Define a versioned interpretation result with operation, arguments, confidence, explanation, ambiguity, and confirmation requirements.
- [ ] Implement deterministic operations for `create_item`.
- [ ] Implement deterministic operations for `create_collection`.
- [ ] Implement deterministic operations for `add_to_collection`.
- [ ] Implement deterministic operations for `set_reminder`.
- [ ] Implement deterministic operations for `complete_item`.
- [ ] Implement deterministic operations for `search_items`.
- [ ] Refactor the existing reminder and group parsers behind one `interpret_capture()` interface.
- [ ] Add a dry-run API that cannot mutate stored data.
- [ ] Publish Phase 4. PR: _pending_. Release: _pending_.

## Phase 5 — Shared preview, correction, and confirmation

- [ ] Show the proposed operation before committing ambiguous text captures on web/PWA.
- [ ] Show the same proposed operation before committing ambiguous text captures on Android.
- [ ] Transcribe audio before showing the operation preview.
- [ ] Allow users to edit the transcription and re-interpret it before saving.
- [ ] Allow users to override the proposal and save a plain item.
- [ ] Require confirmation for uncertain matches, conflicting collections, completion, deletion, and destructive changes.
- [ ] Keep wording, confidence states, and correction actions consistent across web/PWA and Android.
- [ ] Publish Phase 5. PR: _pending_. Release: _pending_.

## Phase 6 — Safe automatic execution

- [ ] Define deterministic confidence thresholds and an auditable reason for automatic execution.
- [ ] Add a user setting for automatic execution of unambiguous commands.
- [ ] Never automatically execute destructive or multiply-matched operations.
- [ ] Record interpreted operations and outcomes in Recent Activity.
- [ ] Provide a clear recovery path for an incorrectly interpreted operation.
- [ ] Test duplicate delivery, retries, offline queues, and idempotency.
- [ ] Publish Phase 6. PR: _pending_. Release: _pending_.

## Phase 7 — Index Ring and Pebble integration

- [ ] Route Index Ring captures through the same interpretation contract.
- [ ] Preserve webhook idempotency and original payload inspection.
- [ ] Return clear operation outcomes through server activity and Android notifications.
- [ ] Support collection addition, reminders, and completion using natural spoken commands.
- [ ] Require confirmation or defer safely when a ring capture is ambiguous.
- [ ] Update Pebble setup documentation and end-to-end tests.
- [ ] Publish Phase 7. PR: _pending_. Release: _pending_.

## Phase 8 — Optional fully self-hosted language model

- [ ] Evaluate whether deterministic parsing leaves enough real-world ambiguity to justify a model.
- [ ] Define supported hardware, memory, storage, latency, and model-download expectations.
- [ ] Keep deterministic operations available when the model is disabled or unavailable.
- [ ] Require the model to emit the same versioned interpretation contract.
- [ ] Prevent free-form model output from directly mutating data.
- [ ] Add privacy, observability, timeout, and fallback controls.
- [ ] Ship only when web/PWA and Android expose equivalent configuration and status.
- [ ] Publish Phase 8. PR: _pending_. Release: _pending_.

## Publication log

| Date | Phase | Pull request | Release | Notes |
| --- | --- | --- | --- | --- |
| _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |

## Decisions still to make

- Whether the internal database and API names should eventually change from Entry/Group to Item/Collection, or remain compatibility details permanently.
- Whether Collections deserve primary navigation or remain accessible through the app menu.
- How collection displays adapt between checklist-like and note-like content without asking users to choose a collection type.
- What recovery mechanism is appropriate for an incorrectly executed non-destructive operation.
- Whether interpreted search questions should appear in Recent Activity without being stored as Items.
