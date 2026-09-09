# FEAT-09: bounded conversational context

## Existing architecture audit

FEAT-07 has one strict `query_context` provider intent. The handler derives the authorized actor from the
application profile, loads normalized JSON, and delegates deterministic resolution and formatting to
`context_queries`. The read-only Context Engine rebuilds actor-visible `EventContext`, `DocumentContext`, and
inferred `TripContext` projections. Event opaque IDs are deterministic hashes of canonical parent type and ID;
trip opaque IDs are deterministic hashes of the component attachment IDs. Query results previously exposed a
resolved trip for trip-card callbacks, but did not expose the resolved event.

Afisha is shared, manual Calendar entries are owner-scoped, projections are excluded, documents inherit their
canonical parent's visibility, and provider `person` is deliberately ignored for authorization. FEAT-07 lists
ambiguity candidates but has no interactive selection state and never picks candidate one.

Existing temporary state is purpose-specific Telegram `user_data`: mutation proposals expire after 15 minutes,
attachment operations and query callback tokens expire, and ConversationHandler states drive active forms. That
state is process-local and is lost on restart. Persisted `meta` previously contained chat IDs and reminder delivery
hashes; JSON writes use the storage lock and atomic file replacement. There was no conversational state. The
provider prompt explicitly classified short fragments such as “а обратно?”, “а где?”, and “а билеты?” as
unsupported conversation.

## Design

This feature adds only the last unambiguously resolved canonical subject for each authorized actor:

```json
{
  "meta": {
    "context_sessions": {
      "vova": {
        "domain": "event",
        "context_id": "evt_<opaque hash>",
        "established_at": "2026-09-09T09:00:00+00:00",
        "expires_at": "2026-09-09T09:30:00+00:00"
      }
    }
  }
}
```

No message, prompt, response, title, document content, Telegram/provider ID, projection, or serialized context is
stored. The 30-minute TTL matches the repository's existing short attachment-operation convention. Expired,
malformed, unknown-actor, unknown-domain, and overlong-TTL entries fail closed and are removed/ignored. The
additive default keeps old files migration-free, and persistence makes valid pointers restart-safe.

A self-contained query replaces the pointer only when exactly one event/trip is canonically resolved. This also
applies to a resolved subject whose requested field is absent. Parser errors, no matches, ambiguity, unrelated
queries, provider failures, and all mutation flows preserve the previous valid pointer. A follow-up never extends
the TTL. If a fresh actor-scoped Context Engine rebuild cannot find the exact opaque ID, the pointer is cleared;
there is no title fallback or fuzzy replacement.

## Explicit v1 compatibility matrix

| Remembered domain | Accepted parsed query types | Effective operation |
| --- | --- | --- |
| event | `event_place`, `event_date`, `event_time` | place, date, time |
| event | `event_documents`, `documents` | event document labels |
| trip | `departure`, `event_time` | outbound departure |
| trip | `arrival`, `return` | outbound arrival, return departure |
| trip | `documents`, `event_documents` | trip document labels |
| trip | `origin`, `destination` | primary outbound endpoints |

All other domain/operation pairs produce a clarification and never search another domain. The strict decoder adds
only an explicit `follow_up` boolean and the bounded trip endpoint operations. Supported fragments are “а где?”,
“какое место?”, “а когда?”, “а во сколько?”, “а билеты?”, “а документы?”, “а обратно?”, “а когда обратно?”,
“а во сколько обратно?”, “а во сколько приезжаем?”, “а откуда?”, and “а куда?”. Contextual mutations (“а удали?”,
“а перенеси?”, “а поменяй время?”), arbitrary fragments, weather, films, and purchases remain unsupported/deferred.

Every handler execution runs the fresh resolution and pointer update/cleanup through `JsonStorage.update`, so actor
entries cannot overwrite one another via a load-modify-save race. Follow-ups remain read-only with respect to all
domain collections and cannot create proposals, notifications, reminders, or mutation targets.
