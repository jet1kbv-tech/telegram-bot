# Smart File Enrichment MVP

Smart enrichment is limited to Event Attachments whose semantic type is `transport_ticket`.
It supports Telegram photos, PDF documents (`application/pdf`), and JPEG/PNG image documents.
The user must press **«✨ Распознать…»** before any file is downloaded or sent outside the bot.

Telegram remains the durable file store. The bot checks the Telegram-reported size, downloads at
most `AI_ATTACHMENT_MAX_BYTES` (8 MiB by default) into memory, submits that content to Polza, and
releases the bytes after the request. It creates no permanent or temporary file, and persists no
bytes, base64, extracted text, or proposal.

Set `POLZA_AI_API_KEY` and the independent `POLZA_ATTACHMENT_MODEL` to enable the feature.
`AI_ATTACHMENT_TIMEOUT_SECONDS` defaults to 25 seconds; `AI_ATTACHMENT_MAX_BYTES` defaults to
8388608. The NL model and timeout are independent. Image input is an `image_url` data URI; PDF
input is a Chat Completions `file` part with a fixed non-user filename and base64 data URI. Both
limits must be positive (and the timeout finite); invalid configuration fails closed at startup.

Only origin, destination, departure date, and departure time are requested and strictly decoded.
Passenger identity and all other ticket details are ignored. Existing non-empty user metadata
wins; AI fills missing fields only. A proposal is held only in conversation memory and the existing
domain update API is called only after **«✅ Сохранить»**. Rejection, stale callbacks, errors, and
manual fallback leave metadata unchanged. Re-analysis does not replace existing values in this MVP;
the user can intentionally replace them through the existing manual editor. Concurrent duplicate
recognition callbacks for one conversation are coalesced and never select an automatic model fallback.

Unsupported types, oversize/download/provider/validation failures, and empty results degrade to
manual editing. There is no OCR, public upload, background analysis, semantic classification,
automatic Phase 2A batch enrichment, attachment retrieval, QR processing, or general document
understanding in this MVP. Logs contain only media/size buckets, result field counts, and bounded
failure categories—not identifiers, filenames, content, raw responses, or extracted values.
