# Phase 6 safe automatic execution

Phase 6 adds conservative, server-owned automation for unattended captures while keeping interactive web, PWA, and Android capture explicit. Typing or recording in an open composer never saves by itself; users still review and commit those captures.

## Policy

Automatic execution is disabled by default. A single setting stored on the server is shown under **Capture automation** in the web/PWA and Android settings screens.

An operation runs automatically only when all of these conditions hold:

- the user enabled automatic execution;
- deterministic confidence is at least `0.95`;
- the interpretation is unambiguous and does not require confirmation;
- the operation is `create_collection`, `add_to_collection`, or `set_reminder`.

Completion, deletion, destructive operations, multi-match results, invalid requests, and uncertain proposals cannot execute automatically. If an unattended capture fails the policy, its original content is safely stored as a plain Item.

## Receipts and recovery

Each automatic interpretation writes a durable operation receipt containing its source key, operation, confidence, policy reason, outcome, affected target, and recovery data. **Recent activity** presents the outcome, reason, and confidence consistently on web/PWA and Android. A reversible receipt offers **Undo**.

Undo can remove an Item or empty Collection created by the operation, or reopen an Item completed through an explicitly accepted operation. Removing a created Collection is refused if it has subsequently gained Items, preserving newer user data. Receipts can be reversed only once.

## Retry safety

Receipts have a unique source key. Repeated delivery returns the original receipt rather than executing the operation again. Native audio captures check this key before transcription, so retrying an already accepted recording does not transcribe it twice. Android's instant-save widget marks unattended captures for this policy and retains its stable key through the offline queue.

The schema is created by the normal idempotent startup migration and is included in the existing whole-database verified backup.
