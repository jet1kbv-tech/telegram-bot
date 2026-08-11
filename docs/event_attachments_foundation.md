# Event attachments foundation

## Existing architecture audit

Calendar events are records in `calendars.vova` and `calendars.sasha`, identified
by their existing `id`. Manual records have `source=manual`. Afisha events are
records in the top-level `afisha` list and also use `id`. An Afisha projection has
a deterministic calendar ID (`cal_afisha_<afisha id>_<owner>`), `source=afisha`,
and `source_id` pointing to the Afisha record. Projections are read-only and are
rebuilt/removed by the Afisha calendar synchronization service.

Legacy tickets remain in `tickets.active` and `tickets.used`. A ticket represents
one dated ticket-domain item, may optionally refer to an Afisha event through
`afisha_id`, and embeds a list of document/photo attachments. Each legacy file
stores `kind`, Telegram `file_id`, `file_name`, and `mime_type`; it does not store
`file_unique_id`. Telegram `file_id` is the sole durable delivery reference, so
files survive application restarts without server-side downloads. The legacy
model therefore has a ticket-centric assumption, but already permits multiple
files on one ticket. It is not changed or migrated in this phase.

## New model and ownership

`event_attachments` is a normalized top-level list. Each record has its own ID,
canonical parent (`calendar` or `afisha` plus event ID), Telegram delivery and
unique IDs, media type, optional file metadata, optional semantic metadata, and
creation metadata. A record can be saved with only its parent, Telegram file,
media type, and the default semantic type `other`.

Manual personal events own `calendar` attachments. Source Afisha events own
`afisha` attachments. Access through an Afisha calendar projection resolves to
the source Afisha event; no attachment is copied into either personal calendar.
The same ownership resolution is used by create, list, and cascade operations.

Missing `event_attachments` data normalizes to an empty list. Existing event,
projection, ticket, and file records are neither rewritten nor migrated. A file
with the same non-empty Telegram `file_unique_id` on the same canonical event is
an idempotent create returning the existing record. The same file on a different
event is allowed.

The domain API is in `bot.services.event_attachments` and contains no Telegram
calls: `create_event_attachment`, `get_event_attachment`,
`list_event_attachments`, `get_attachments_for_event`,
`delete_event_attachment`, and `delete_attachments_for_event`.

## Native scope and follow-up

Calendar and Afisha cards expose **📎 Документы**. The native flow accepts one
document or photo, asks for semantic type, and asks for transport type only for a
ticket. The stored `file_id` is sent directly through `send_document` or
`send_photo`. Deletion has a separate confirmation. Event deletion warns when
files exist and cascades only attachment records; Telegram-hosted files are not
deleted.

Optional destination/person/comment metadata editing and Telegram media-group or
multi-document batching are intentionally deferred. The collection and API
already support any number of attachments, so later resumable and AI flows do
not require a storage redesign.

Legacy Tickets and event attachments coexist independently. A safe later
convergence is to turn Tickets into a global read view over event attachments
plus unchanged legacy tickets, with an explicit, separately reviewed migration
only if one is eventually needed.
