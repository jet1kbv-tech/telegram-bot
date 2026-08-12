# Natural-language event attachment mutations

`delete_event_attachment` and `update_event_attachment` are canonical intents
separate from event mutations. Their identifying fields are the retrieval
contract (`target`, type, route, departure date, person and direction); the
provider never supplies storage identifiers. Update replacement values use
`new_*` fields and only explicitly supplied fields are changed.

The application resolves an optional event with the existing attachment-event
resolver and resolves documents exclusively with `event_attachment_query`.
No provider call occurs while choosing an event/document or confirming. Manual
calendar visibility remains owner-only; Afisha and calendar projections use the
canonical Afisha parent and therefore mutate one canonical attachment.

Dates are normalized by `nl_dates.resolve_date_expression`; departure and
arrival times must be exact `HH:MM`. Invalid input fails before a proposal is
created. A bounded, actor-bound, 15-minute pending operation contains only its
operation token, intent, query, candidate identifiers, validated changes and
expiry. It contains no file bytes, Telegram file identifiers, filenames or
provider output, and is cleared after success, cancellation, menu navigation,
expiry, and stale callbacks.

Delete and update always produce a confirmation. Confirmation rechecks current
visibility and calls the existing domain service through the atomic storage
update API. A repeated callback is harmless because the pending operation has
already been removed. Update proposals show only requested before/after values;
equal values result in the controlled “already set” response.
