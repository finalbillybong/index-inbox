# Unified Items and Collections contract

Status: Phase 1 product and compatibility contract. This document defines the target language and behaviour; it does not rename the current API or database.

## Product definitions

- **Item** is the universal stored object. An Item may contain a title, text, audio, category, tags, a Collection, a due time, and state.
- **Collection** is a named container for related Items. It is the future user-facing name for the current Group concept. A Collection is not itself a note or checklist, and an Item can exist without one.
- **Reminder** is an Item with a due time. It is a view and behaviour applied to an Item, not a separate stored content type.
- **Completed** means the work represented by an Item is finished. Completion is independent of whether the Item has a reminder.
- **Processed** means the user has reviewed or triaged the Item. Processing never implies completion, and completion never implies processing.
- **Archived** removes an Item or Collection from normal active views without deleting it.
- **Capture** is the shared input for text or audio. A later interpretation layer may propose an operation, but users must always be able to save a plain Item.

Until the compatibility migration is shipped, `entry`, `group_name`, `note_groups`, and `/api/groups` remain stable internal and API terms. Existing `reminder_completed` continues to mean that a reminder occurrence was dealt with; it must not be silently reused as general Item completion.

## Shared navigation contract

The responsive web app, installed PWA, and native Android app use the same information architecture:

1. **Inbox** is the default view of active Items.
2. **Capture** is a primary action available without navigating through settings.
3. **Reminders** is a filtered Item view, not a separate store.
4. **Collections** provides named containers, their lifecycle, and chronological contents.
5. Search and filters refine the current Item set and can be cleared in one action.
6. Recent Activity and Settings live in the app menu; account and destructive maintenance actions are not primary navigation.
7. Back closes the most recent dialog or secondary screen before leaving or minimising the app.

The same destination names, state meanings, and core actions must be used on every client. Layout may adapt to screen size, and platform-only facilities such as Android widgets may remain platform-specific.

## Shared Item-card contract

Collapsed cards show enough information to identify and act on an Item: title or useful fallback, text preview or audio state, category, timestamp, star state, Collection when present, and reminder summary when present.

Expanded cards expose the same core operations on every client:

- edit title and text;
- play and download audio when present;
- edit category and tags;
- assign, move, or remove the Collection;
- star or unstar;
- mark Processed or Unprocessed;
- set, change, remove, or complete a reminder;
- archive or restore;
- inspect the original payload;
- delete with confirmation.

Future general completion must be a separate control from Processed and reminder completion. State must be visually distinguishable by more than colour alone. Missing or remotely deleted Items must reconcile cleanly instead of remaining stuck locally.

## Interpretation and confirmation boundary

Capture interpretation will be server-owned and deterministic before any optional language model is considered. All clients will consume the same versioned result. Ambiguous, destructive, or multiply matched operations require confirmation; a dry run cannot mutate data. Audio is transcribed and its text can be corrected before interpretation and save.

## Acceptance rule

A user-facing capability is complete only when server/API support, responsive web, standalone PWA, and Android behaviour are implemented where applicable, terminology and actions match, existing installations upgrade safely, automated tests pass, and documentation is current.
