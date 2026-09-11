# Smart Upcoming Brief v1

`upcoming_brief` is a bounded `query_context` operation over the existing,
actor-scoped Context Engine. The provider classifies the operation and may copy
an explicit date expression or person scope; application code resolves dates,
selects canonical objects, joins documents and renders every word of the brief.
Generated briefs are not persisted.

Without an explicit expression the interval is exactly seven local calendar
days including today. Explicit dates, same-month named ranges, weekends and ISO
week ranges reuse `nl_dates`. Events qualify when their BUG-01 effective
lifetime overlaps the inclusive interval. Results contain at most eight events,
ordered by start, canonical parent type and canonical ID, followed by an exact
remainder count.

Manual Calendar events come only from the application-derived actor. Active
Afisha is shared. Calendar projections of Afisha, inactive Afisha, duplicate
canonical identities, and source events linked to a displayed trip are omitted.
`person=self` (or the actor's own key) keeps the actor-visible bundle;
`person=both` or another actor name narrows events to shared Afisha and admits a
trip only when it has a shared linked source event. Provider output never loads
another Calendar.

Trips use FEAT-08's reliable interval and overlap semantics. Document output is
positive-only: a trip can report outbound/return transport tickets from their
canonical direction metadata, while any non-trip event attachment is described
only as an attachment. No readiness or missing-document claim is generated.

Weather is optional. Exactly one displayed trip may provide the single target,
using its canonical destination and the intersection of the trip and requested
interval. The existing provider horizon is checked before one range request.
No trip, multiple trips, an invalid interval, provider failure, or incomplete
forecast causes silent weather omission while preserving the core brief.

The brief has no FEAT-09 domain and no subject pointer. Executing it does not set,
clear, or overwrite conversational context. The Telegram path uses
`storage.load()` rather than `storage.update()`, so even normalized storage is
not rewritten for this read-only query.

Proactive weekly delivery is deferred. The existing scheduler has a dedicated
trip reminder lifecycle but no per-actor weekly-summary preference or schedule;
adding those safely would exceed a small reuse and risk introducing the generic
notification settings architecture excluded from this feature.
