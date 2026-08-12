# Film preference foundation

## Architecture audit

Films are records in the top-level `films` list and are normalized by
`normalize_film`. Their canonical fields are identity/title, `want` or
`watched` status, author/comment, retained numeric rating fields, and optional
provider metadata (provider and external ID, movie/TV type, localized and
original titles, year, genres, description, and external rating). Enrichment
updates that same metadata. Lists are status-filtered and open a compact detail
card; genre and random flows operate only on `want` films and use the shared
atomic status/delete operations.

The current actor is resolved from the allow-listed Telegram username through
the profile's canonical `wishlist_owner` (`vova` or `sasha`). Reaction callbacks
reuse that resolver and never accept an actor from callback or provider data.

An older numeric 1–10 system remains in storage as `sasha_rating`,
`vova_rating`, `legacy_rating`, and the older `rating` key. The checked
production snapshots contain 51–53 films and five non-null values for each of
`sasha_rating` and `vova_rating`. This foundation intentionally leaves those
fields untouched.

Watched films may contain an optional, per-user reaction map:

```json
{"reactions": {"vova": "like", "sasha": "neutral"}}
```

The only canonical values are `like`, `neutral`, and `dislike`; the only actor
keys are `vova` and `sasha`. A missing actor key means that person has not
reacted. A missing `reactions` object is also valid and is retained for legacy
records.

For future recommendation use, `like` is a positive preference signal,
`neutral` is a weak/neutral signal, and `dislike` is a negative preference
signal. Unknown is not neutral: it supplies no taste signal. A watched+unknown
film remains watched (and therefore must not be proposed as unwatched), but it
must not influence preference ranking.

The films menu exposes an actor-specific count of watched+unknown films and an
optional deterministic backlog-rating session. The session follows storage
order, keeps visited/skipped IDs only in Telegram session memory, and never
persists skips. Re-entering recalculates eligibility from current storage.

Historic numeric `sasha_rating`, `vova_rating`, `legacy_rating`, and `rating`
fields remain readable and are preserved by existing normalization. Numeric
ratings are not translated into reactions and reactions do not produce a
combined score.

Handlers use the atomic `set_film_reaction` and `clear_film_reaction` domain
operations. Reactions remain attached to a film across status changes and are
removed naturally when the film is deleted. This representation lets a future
recommendation service group each user's liked, neutral, and disliked films
while reading the film's existing genres, media type, year, and other metadata. No recommendation or ranking behavior is part of this foundation.

## Natural language scope

Natural-language film mutations currently resolve `update_film` targets and
support status/comment changes. Adding a new provider intent would require a
schema, decoder, prompt, proposal, and mutation-contract expansion. Reaction NL
is therefore deferred rather than creating a parallel resolver; button flows
provide the complete foundation without extra AI calls.
