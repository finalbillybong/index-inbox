# Index Inbox roadmap

This file tracks the agreed implementation sequence. Each phase is developed on its own branch, tested on Unraid, and merged before the next phase begins.

## Progress

- [x] Phase 0 — Stabilize the baseline
- [x] Phase 1 — Group lifecycle and manual organization
- [x] Phase 2 — Live capture feedback
- [x] Phase 3 — Group timeline and per-group export
- [ ] **Phase 4 — Suggested grouping (in progress)**
- [ ] Phase 5 — Cloudflare-aware client IP handling
- [ ] Phase 6 — Backup status and restore verification
- [ ] Phase 7 — Playwright browser tests
- [ ] Phase 8 — Final Unraid regression and release

## Phase 1 — Group lifecycle and manual organization

- [x] Rename a group atomically with its entries and aliases
- [x] Archive/close a group so voice matching stops
- [x] Reopen an archived group
- [x] Assign a standalone note to an active group
- [x] Move a note between active groups
- [x] Remove a note from a group without deleting it
- [x] View, add, and remove spoken aliases
- [x] Reject alias and group-name conflicts clearly
- [x] Record lifecycle operations in activity history
- [x] Add migrations, API tests, UI controls, and documentation
- [x] Validate on Unraid

Removal remains non-destructive: removing a group converts its entries to standalone notes and never deletes audio or payloads.

## Phase 2 — Live capture feedback

Add typed, deduplicated browser notices for standalone captures, grouped captures, group creation, repeated commands, unmatched commands, and ingestion errors without exposing full note text.

- [x] Return typed, incremental events from the change polling endpoint
- [x] Show dismissible in-app notices without interrupting editing
- [x] Deduplicate notices across polling cycles
- [x] Report standalone captures and grouped captures separately
- [x] Report new groups, repeated create commands, and unmatched create commands
- [x] Report rejected webhooks and ingestion failures without exposing note content
- [x] Add API tests, browser syntax checks, and documentation
- [x] Validate on Unraid

## Phase 3 — Group timeline and export

Add a dedicated chronological group view with audio and editing, plus Markdown, JSON, and ZIP/audio exports scoped to one group.

- [x] Add an authenticated chronological timeline endpoint for each group
- [x] Add a dedicated group timeline view with transcription, tags, category, and audio controls
- [x] Add group-scoped Markdown and JSON exports
- [x] Add group-scoped ZIP exports containing metadata, Markdown, and available audio
- [x] Keep empty and archived groups accessible from the group manager
- [x] Add API tests, browser syntax checks, and documentation
- [x] Validate on Unraid

## Phase 4 — Suggested grouping

Offer conservative, user-confirmed group suggestions for near-matching prefixes. Never silently assign uncertain captures or learn aliases without approval.

- [x] Detect only leading group-like identifiers with an exact numeric match
- [x] Require a conservative similarity threshold for the name portion
- [x] Present suggestions for explicit acceptance or dismissal
- [x] Persist dismissals so rejected suggestions do not return
- [x] Never create aliases from suggestions
- [x] Add tests, UI documentation, and Unraid validation steps
- [ ] Validate on Unraid

## Phase 5 — Cloudflare-aware client IP handling

Trust forwarding headers only through explicitly configured proxy hops, keep direct peer information, and apply login throttling to the resolved visitor address.

## Phase 6 — Backup status and restore verification

Track backup requests and outcomes, create verifiable manifests, expose status in the UI, and document a safe staging restore test.

## Phase 7 — Playwright browser tests

Cover first-run setup, login, capture refresh, group lifecycle, suggestions, timelines, exports, and mobile behavior in CI.

## Phase 8 — Final release validation

Back up production, deploy the release candidate to Unraid, verify migrations and Cloudflare access, test all critical flows, restore a test backup, and tag the known-good release.
