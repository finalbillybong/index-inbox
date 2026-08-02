# Phase 5 capture preview and confirmation

The responsive web app, installed PWA, and native Android app now use the same server interpretation before committing a capture. The preview uses shared operation labels and four confidence states: **High confidence**, **Check proposal**, **Confirmation required**, and **Needs correction**.

Text edits are re-interpreted after a short pause. Audio is transcribed on the self-hosted server first; the editable transcription then enters the same preview flow. No recording or text is sent to an external interpretation service.

Ambiguous proposals disable the normal save action. The user can correct the text and receive a new proposal, or choose **Save as plain Item** to bypass interpretation explicitly. A unique consequential operation such as completing an Item shows **Confirm operation** and cannot execute through the preview-aware API without that confirmation.

The manual capture API accepts an `interpretationAction` value:

- `accept` commits a non-ambiguous proposal that does not require confirmation;
- `confirm` explicitly authorizes a unique proposal that requires confirmation;
- `plain` stores the submitted text as a standalone Item without interpreting it.

Older API clients that omit this field retain their previous behaviour. Android pending captures preserve the selected action across offline retries. Deletion and destructive Collection controls continue to use their existing explicit confirmation dialogs.

Phase 5 does not add automatic execution policy. Confidence thresholds, automatic execution settings, recovery, and operation outcome auditing remain Phase 6 work.
