# AI Features — Audio Transcription v1

## Product flow

An authorized actor opens **🤖 AI-фичи → 🎙 Расшифровка аудио**, sends media, and immediately
gets an upload/processing acknowledgement. Processing continues outside the conversation handler.
The result is delivered only as a DOCX to the submitting chat, with duration and anonymous speaker
count in its caption; the transcript is not posted as Telegram text.

## Architecture and provider contract

`AiesaTranscriptionService` is the only Aiesa boundary. It signs `{public}\n{unix_timestamp}` with
HMAC-SHA256 and sends `X-Public-Key`, `X-Timestamp`, and `X-Signature`. It uploads multipart media to
`POST https://api.transcription.aiesa.ru/api/v2/transcriptions`, requires HTTP 201 and a
`transcription_id`, polls `GET .../{id}`, then retrieves the HTTPS URL at `data.results.json`.
Responses are validated and converted to typed statuses, results, and segments. Provider payloads
never escape into Telegram orchestration code.

## Durable asynchronous lifecycle and recovery

After Telegram download and Aiesa creation, the media is deleted and a minimal `ai_jobs` record is
atomically persisted in the existing JSON store. It contains actor/chat routing, provider ID,
safe filename, timestamps, retry counters, and state—not audio or transcript content. The PTB
JobQueue checks work every 15 seconds by default. States are `processing`, `postprocessing`,
`delivering`, `completed`, and `failed`. Restarts resume the existing provider ID and therefore do
not create a second paid transcription. Completed records are never delivered again.

Telegram has no idempotency key for `send_document`: a host crash after Telegram accepts a document
but before `completed` is persisted leaves a very small possible duplicate-delivery window. The
durable `delivering` marker makes this observable and ordinary send failures are retried.

Transient network failures, HTTP 429, 5xx, and result download failures use persisted exponential
backoff capped at 15 minutes and eight attempts. Permanent 4xx/provider failures become terminal.
Normal provider processing is polled no faster than the configured scheduler interval.

## Diarization and deterministic normalization

`SPEAKER_01`, `SPEAKER_02`, etc. map only to `Спикер 1`, `Спикер 2`; identity is never inferred.
Missing/malformed labels map to `Спикер неизвестен`. Empty text is dropped. Remaining segments sort
by numeric `(start, end, provider id)`, not provider array order. Adjacent turns merge only when they
have the same speaker, do not overlap, and the gap is at most two seconds. Overlaps are retained as
separate turns. Merging concatenates text without loss and retains the first navigation timestamp.

## Polza cleanup and fallback

Normalized turns are deterministically chunked at a 12,000-character estimate, only between turns.
Every entry has stable `id`, `speaker`, and `timestamp`. Polza is instructed to edit punctuation,
capitalization, and only highly certain ASR errors—not summarize, omit, translate, censor, or invent.
JSON output is accepted only if every ID appears once in the original order with unchanged speaker
and timestamp and non-empty text. Each segment also passes a deterministic preservation guard over
case-folded Unicode word tokens: punctuation/case-only edits pass directly; other edits must contain
no standalone token insertion/deletion, retain at least 75% of the token count, and meet token-sequence
and punctuation-free character similarity floors of 0.60 and 0.82. These conservative floors allow a
close 2-to-1 recognition repair such as `стрик холдеры` -> `стейкхолдеры`, while rejecting shortening
and material rewriting. A segment that fails only this text guard falls back to its normalized Aiesa
text without discarding accepted siblings. Structural/malformed output or a provider failure falls
back for the bounded chunk; other chunks remain cleaned. Logs contain only aggregate acceptance,
rejection reasons, and provider-failure categories—never transcript or provider payload text.

## DOCX

The document uses Arial and contains title, sanitized source filename, processing date, duration,
speaker count, and chronological `[HH:MM:SS] Спикер N` turns. Output names follow
`Расшифровка_<safe-name>_<YYYY-MM-DD>.docx` and never contain internal IDs.

## Media, limits, and long recordings

Direct upload supports MP3, M4A, MP4, WAV, OGG/Opus, WebM, and MOV from Telegram audio, voice,
document, video, or video note. No ffmpeg, Whisper, transcoding, or local CPU-heavy processing is
used. The default application limit is 50 MB (also bounded by Telegram Bot API/provider limits),
configurable with `AI_TRANSCRIPTION_MAX_FILE_MB`. One-hour recordings are supported when within
those limits because polling and chunked cleanup are asynchronous.

## Temporary files and privacy

Media and output use unpredictable OS temporary directories set to mode 0700. Media is removed
immediately after provider upload; result JSON is held only in memory; DOCX is removed after the
delivery attempt. No credentials, signatures, Telegram file IDs, media, transcript text, utterances,
prompt/response bodies, result URLs, or full provider payloads are logged or persisted. Logs contain
only structural provider state, latency/error category, duration/billing, counts, cleanup outcome,
and delivery outcome.

## Configuration

Required: `AIESA_API_PUBLIC`, `AIESA_API_SECRET`. Optional:
`AIESA_TRANSCRIPTION_POLL_SECONDS` (15), `AI_TRANSCRIPTION_MAX_FILE_MB` (50), and
`AI_TRANSCRIPTION_CLEANUP_MODEL` (falls back to `POLZA_AI_MODEL`). Cleanup additionally needs
`POLZA_AI_API_KEY`. Missing Aiesa credentials disable invocation gracefully; missing Polza settings
use the normalized Aiesa transcript.

## Production smoke checklist

1. Install updated requirements in the application virtualenv.
2. Set Aiesa credentials and optionally the cleanup model; never print their values.
3. Restart one bot instance and confirm the startup structural enablement message.
4. Confirm both authorized actors see the AI menu and an unauthorized username is rejected.
5. Submit a short two-speaker OGG and verify anonymous speakers, timestamps, Cyrillic, and DOCX.
6. Submit supported document/audio/video samples and verify unsupported/oversized bounded errors.
7. Restart while a longer job is processing; verify the same provider ID resumes and one DOCX arrives.
8. Simulate provider 429/5xx and Telegram send failure; verify backoff and eventual delivery.
9. Inspect JSON/logs/temp storage to confirm no media, transcript, file ID, secret, or prompt content.
