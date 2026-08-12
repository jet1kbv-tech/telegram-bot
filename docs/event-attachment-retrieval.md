# Event Attachment Retrieval — Natural Language MVP

## Architecture audit

Attachments live only in normalized `event_attachments`. Calendar projections
whose `source=afisha` resolve through `resolve_attachment_parent` to the single
Afisha-owned collection; legacy Tickets are not queried. Smart Enrichment's
canonical transport fields are consumed without changing enrichment or storage.
The native display helpers remain the source for ticket cards and labels, and
Telegram delivery reuses stored file IDs with `send_document`/`send_photo`.

The provider boundary has one read-only `query_event_attachments` intent. It
structures nullable `target`, `semantic_type`, `transport_type`, `origin`,
`destination`, departure `date`, explicit `person`, advisory `direction`, and
`return_all`; it never receives stored titles or selects storage records.

## Deterministic query rules

`event_attachment_query` is a pure service over a detached storage snapshot. It
returns `none`, `single`, or `multiple`, a bounded stable-order candidate tuple,
total/bounded metadata, and per-candidate match reasons. Canonical enum, person,
and departure-date filters use trimmed case-insensitive exact equality. A date
never compares with arrival, event, creation, or purchase dates.

Route values use NFKC Unicode normalization, case folding, Russian `ё` → `е`,
punctuation-to-space conversion, and whitespace collapse. Every complete query
token must be present among the stored complete tokens. Thus `Москва` matches
`Москва Казанская`, and `Воронеж` matches `Придача Воронеж Южный`, while
`Москва` does not match `Московская`. There is no stemming, edit distance,
embedding, provider call, or creation-order direction guess.

## Resolution, access, and pending state

An exact target restricts the pure query to its canonical parent. Multiple exact
events use a bounded chooser; zero exact events uses the existing bounded
upcoming-event fallback. A chooser selection reruns only the local query and
never the provider. Without a target, the query spans the requester's manual
calendar plus shared Afisha, preserving existing visibility: private manual
events belong to their calendar owner; active Afisha is shared. Projections do
not create duplicate attachment candidates.

Pending state contains an opaque operation token, actor, expiry, structured
query, canonical parent IDs, and bounded candidate IDs only—no provider dump,
file bytes, file metadata, or Telegram IDs. Cancel/menu/expiry clears it.

## Result and delivery UX

No match produces a controlled empty result. One match previews the existing
attachment card with send/open/close actions. Multiple matches and `return_all`
use bounded, numbered labels built by existing display helpers. Delivery sends
the stored Telegram ID directly as its stored media type, performs no download,
AI call, or mutation, rechecks visibility, and reports a controlled Telegram
failure.

The query result is intentionally independent of Telegram and file delivery.
Future delete/update intents can consume the same candidate IDs, outcomes, and
match reasons, adding their own confirmation and mutation boundary without
copying attachment matching.

## Privacy

New logs contain only intent, outcome, and candidate count, plus a structural
Telegram delivery failure. They exclude routes, people, dates/times, titles,
names, attachment IDs, Telegram IDs, and raw provider output.
