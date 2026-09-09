# BUG-03: audit of user-facing AI operations

## Minimal interaction contract

Noticeably slow operations create one status message (`⏳ Обрабатываю…`, with a
more specific verb where useful). The operation edits that message into its
domain result or a concise `⚠️` failure. When the final result must be a separate
Telegram object, such as a DOCX, the status is removed after successful
delivery. Provider names, status codes, exceptions, and private input are never
included in user copy.

A `🔄` action is offered only for a read-only provider call whose bounded input is
already available. Mutation proposals remain confirmation-first and are not
blindly retried. This is deliberately a handler-level convention rather than a
workflow framework.

## Flow audit and classification

| Flow | Trigger and implementation | Timing and current lifecycle | Retry and side-effect analysis | Class / decision |
|---|---|---|---|---|
| Hybrid audio transcription | Audio/video sent in AI transcription state; `receive_media` creates provider work and `process_transcription_jobs` polls it. Asynchronous, persisted job. | Download/job creation and later processing are noticeable. A status message is created; DOCX is the success object; definitive failures resolve the status. Technical detail stays in logs. | Raw media is deleted after job creation and is not persisted. Poll/result retries remain internally bounded, but a terminal job cannot be meaningfully restarted. No button; ask the user to resend audio. | **B**, lifecycle unified; terminal manual retry intentionally unavailable. |
| NL intent parsing and assistant routing | Any authorized text/caption handled by `nl_text_handler`; Polza parser followed by deterministic query/proposal routing. Synchronous and potentially several seconds. | Existing `⏳ Разбираю команду…` is edited into every normal success/failure through `_WaitingResponse`. Errors are already user-safe. | Original text is not retained for callbacks. More importantly, a parsed command may lead to a mutation proposal. No retry button; resend/rephrase is explicit. Confirmed writes remain separate and exactly-once guarded. | **B**, already compliant; unchanged. |
| NL calendar/Afisha/purchase/film mutations | Intent parser creates a proposal; user confirmation invokes domain action services. | Slow portion uses the NL status. No write occurs until confirmation; success remains domain-specific. | Blindly rerunning the whole utterance could duplicate or ambiguously repeat a write. Confirmation callbacks already consume/validate proposal state. | **B**, no blind retry; unchanged. |
| Ticket/document image enrichment (native) | User selects automatic recognition; `_recognize_ticket` downloads the Telegram file and calls `PolzaTicketEnricher`. Synchronous and noticeable. | Existing `🔎 Анализирую билет…` is edited into a proposal or safe failure/manual fallback. No write occurs before explicit save. | File ID/draft usually remains, but stale draft/parent state and the existing manual fallback make a new generic retry contract unnecessary. Re-entering recognition through the existing UI is safe. | **A**, already compliant; unchanged to avoid redundant controls. |
| Ticket enrichment from an NL attachment operation | User chooses analysis on a pending attachment; `nl_event_attachments` calls the same enricher. | Existing status is edited to proposal or safe failure; attachment remains a draft until confirmation. | Provider-only repetition is safe while the actor-bound pending operation exists; existing analysis action can be selected again. No additional retry persistence required. | **A**, already compliant; unchanged. |
| Film recommendation discovery | Native picker or parsed read-only recommendation; `MovieRecommendationService` uses TMDB candidate discovery and deterministic ranking. | Native callback is edited to `✨ Подбираю варианты…`; NL reuses its status. Result/failure replaces that message. | Discovery is read-only. Actor, filters, source, and short-lived user-scoped session are sufficient; no private query or object write is replayed. Retry is safe and authorization is rechecked by the callback router. Adding a recommended film remains a separate idempotent action. | **A**, safe retry retained and fixed to work after first-call failure. |
| Transcript cleanup | Optional Polza cleanup inside a transcription job. | Not independently user-triggered; best-effort fallback preserves the main result. | Retried only as part of existing internal post-processing policy. | **D**, internal stage; no separate UX. |
| Master transcript generation/alignment | Parallel Polza master call inside transcription creation, then local alignment. | Not a separate operation; failure falls back to Aiesa output. | Internal fallback only. | **D**, no separate UX. |
| Film metadata search/enrichment | Film add/backlog enrichment handlers use TMDB metadata APIs. | Existing edit-in-place search/proposal/failure UX. | External metadata lookup, not a generative AI/model call; writes require confirmation or use duplicate guards. | **D**, provider-backed but not AI; unchanged. |
| Weather/context notification enrichment | Scheduled reminder rendering may add deterministic context and weather API data. | Background, not initiated as an AI operation; core reminder survives enrichment failure. | No user retry is appropriate. | **D**, not AI and not a user operation. |

No other user-triggered model translation or cleanup flow exists. Film title and
genre normalization are deterministic code, not model calls.

## Persistence and privacy decision

The transcription job stores only resumable, non-content metadata, now including
the Telegram status message identifier needed to resolve the lifecycle after a
restart. It does not store audio, transcript text, captions, or user messages.
Recommendation retry state remains short-lived in `user_data` and contains only
normalized filters and actor/source identifiers.
