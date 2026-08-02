# Repository Agent Instructions

## Unified Items and Collections programme

Before planning or changing Items, Collections, Groups, reminders, completion, capture interpretation, Index Ring ingestion, or cross-client navigation, read `UNIFIED_ITEMS_ROADMAP.md` in full.

Treat that roadmap as the source of truth for:

- product terminology and distinctions;
- implementation order;
- backward-compatibility requirements;
- responsive web, installed PWA, and native Android feature parity;
- safety and confirmation behaviour;
- completion and publication status.

For every task covered by the roadmap:

1. Add the relevant roadmap work to the current implementation plan.
2. Preserve the distinction between **Processed** and **Completed**.
3. Do not introduce a user-facing feature on only web/PWA or only Android unless the roadmap explicitly marks it as platform-specific.
4. Treat responsive web and installed PWA verification as separate checks even though they share source code.
5. Preserve existing entries, groups, reminders, audio, integrations, exports, and API compatibility unless a reviewed migration explicitly changes them.
6. Do not check off roadmap work merely because it was implemented locally, committed, pushed, or opened as a pull request.
7. Check off an item only after it is merged into `main`, every applicable parity requirement passes, and any required release is published.
8. When checking off work, add the merged pull request and release tag to the relevant phase and append a dated row to the Publication log.
9. If only part of a roadmap checkbox is delivered, leave it unchecked and add a concise indented progress note beneath it.
10. Mention roadmap progress and remaining unchecked work in the final handoff for any roadmap-related task.

If implementation reveals that the roadmap should change, update the written plan explicitly and explain the decision. Do not silently drift from it.
