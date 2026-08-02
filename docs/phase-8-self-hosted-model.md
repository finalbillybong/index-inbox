# Phase 8 optional self-hosted interpretation model

## Evaluation

Deterministic parsing remains faster, auditable, and sufficient for explicit Collection, reminder, completion, and search commands. Its meaningful gap is indirect phrasing such as “could you put milk on my shopping list?”, where silently treating prose as an operation would be unsafe but a reviewable proposal is useful. This justifies an optional fallback, not replacement of the parser.

Ollama was selected as the documented runtime because its local `/api/chat` endpoint accepts a JSON schema and temperature control. The adapter remains narrow enough that another local runtime can be added later. The Compose service is behind an explicit `model` profile, sets `OLLAMA_NO_CLOUD=1`, and has no public port.

## Safety contract

- The model is disabled by default and cannot run until URL and model name are configured on the server.
- Deterministic interpretation runs first. The model is considered only for ambiguous or plain-Item results.
- Captures are sent only to the configured runtime URL. No hosted provider is included.
- Output must be valid schema-shaped JSON and one of the allowed non-destructive contract operations: create a Collection, add to a Collection, set a reminder, or search.
- Collection names are resolved against current server data. Completion remains deterministic-only so a second model inference can never select a different existing Item.
- Reminder timestamps must parse and be in the future.
- Model confidence is capped at `0.70`, `requiresConfirmation` is always true, and automatic execution therefore rejects every model proposal.
- Network errors, invalid output, missing models, and the hard timeout return the original deterministic result.
- Error status records exception type but never capture text or raw model output.

## Resources and operations

The runtime and model are not bundled into the Index Inbox image. A small quantized 4B model is the starting recommendation for CPU-only self-hosters with roughly 8 GB free RAM. Larger models need correspondingly more RAM/VRAM, disk, and inference time. The configured timeout is constrained to 1–30 seconds so capture preview cannot hang indefinitely.

Web/PWA and Android Settings expose the same configured/enabled/runtime state, connection test, and safety explanation. URL and model name remain environment-owned because changing a server-side inference destination is an administrator action.
