# Index Inbox roadmap

This file tracks the agreed implementation sequence. Each phase is developed on its own branch, tested on Unraid, and merged before the next phase begins.

## Progress

- [x] Phase 0 — Stabilize the baseline
- [x] Phase 1 — Group lifecycle and manual organization
- [x] Phase 2 — Live capture feedback
- [x] Phase 3 — Group timeline and per-group export
- [x] Phase 4 — Suggested grouping
- [x] Phase 5 — Cloudflare-aware client IP handling
- [x] Phase 6 — Backup status and restore verification
- [x] Phase 7 — Playwright browser tests
- [ ] **Phase 8 — Final Unraid regression and release (promotion in progress)**

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
- [x] Validate on Unraid

## Phase 5 — Cloudflare-aware client IP handling

Trust forwarding headers only through explicitly configured proxy hops, keep direct peer information, and apply login throttling to the resolved visitor address.

- [x] Disable forwarded client-IP trust by default
- [x] Require explicit proxy-hop and trusted-peer network configuration
- [x] Resolve Cloudflare and standard forwarded client addresses only from trusted peers
- [x] Store both resolved visitor and direct peer addresses for login attempts
- [x] Apply local-login throttling to the resolved visitor address
- [x] Add configuration validation, tests, and deployment documentation
- [x] Validate the secure default on Unraid; trusted-proxy resolution remains optional

## Phase 6 — Backup status and restore verification

Track backup requests and outcomes, create verifiable manifests, expose status in the UI, and document a safe staging restore test.

- [x] Create consistent SQLite snapshots with stored audio bundles
- [x] Include SHA-256 manifests and reject incomplete or modified archives
- [x] Track backup requests, success, failure, size, and completion time
- [x] Expose backup status, creation, and latest-download controls
- [x] Add a read-only CLI verification workflow
- [x] Retain a bounded number of local backup archives
- [x] Add tests, documentation, and Unraid restore validation
- [x] Validate on Unraid

## Phase 7 — Playwright browser tests

Cover first-run setup, login, capture refresh, group lifecycle, suggestions, timelines, exports, and mobile behavior in CI.

- [x] Run browsers against an isolated temporary local-auth server
- [x] Cover first-run owner setup and subsequent login
- [x] Cover live webhook refresh and capture notices
- [x] Cover group lifecycle and suggestion review
- [x] Cover timeline editing and group downloads
- [x] Cover narrow mobile viewport behavior
- [x] Add Chromium CI execution and contributor documentation
- [x] Validate locally and in GitHub Actions

## Phase 8 — Final release validation

Back up production, deploy the release candidate to Unraid, verify migrations and Cloudflare access, test all critical flows, restore a test backup, and tag the known-good release.

- [x] Prepare the `v1.0.0-rc.1` candidate and release checklist
- [x] Complete and automate the final narrow-screen UX regression
- [x] Create and independently verify a pre-release production backup
- [x] Deploy `v1.0.0-rc.1` without replacing the persistent data path
- [x] Verify database migrations, health, local authentication, and Cloudflare access
- [x] Run the critical capture, grouping, timeline, export, and backup regression
- [x] Verify rollback readiness and complete an isolated restore check
- [ ] Confirm GitHub Actions and the release-candidate pull request are green
- [ ] Promote the tested candidate to `v1.0.0` and tag the merge commit
