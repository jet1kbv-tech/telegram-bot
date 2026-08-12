# Movie Recommendations v1 — engine foundation

## Provider audit and decision

The existing catalogue is **TMDB API v3**, configured with
`TMDB_API_READ_ACCESS_TOKEN`. It already supports movie and TV search and
details (`/search/movie`, `/search/tv`, `/{media_type}/{id}`), returning TMDB
identity, media type, localized/original title, year, genres (details), overview
and external rating. The integration uses an 8 second bounded timeout (3 second
connect, 6 second read), no automatic retry, and converts HTTP, malformed JSON,
and unavailable responses into domain errors. TMDB applies the rate limits of
the account/API; HTTP 429 is treated as unavailable rather than retried.

TMDB also supplies the official `/discover/movie` and `/discover/tv` endpoints,
vote count, popularity, language/country, dates and runtime constraints. It is
therefore reused rather than adding a duplicate provider. Recommendations use
the separate `TMDB_API_TOKEN`, with the existing read-access token as a
backwards-compatible fallback. Search/details enrichment remains independent.

## Domain and algorithm

`RecommendationCandidate` is a transient, provider-independent value containing
provider identity, movie/TV type, titles, year, genre identities, overview,
rating, votes, popularity, optional runtime, language and countries. Candidate
pools are never written to `data.json`.

Only watched films and explicit reactions form profiles. Reaction weights are
centralized as like `+1`, neutral `0`, dislike `-1`; unknown has no taste weight
and legacy numeric ratings are ignored. Unknown watched titles are nevertheless
excluded. Each genre and media type retains its raw weighted sum and evidence
count. Its score is `weighted_sum / (evidence + 2)`, a conservative shrinkage
toward zero. Media-type preference is used only after three pieces of evidence.
The profile exposes like, neutral, dislike, reacted, and unknown-watched counts.
Year/era, language, and country profile features are deferred because stored
coverage is not reliably sufficient.

Single-person taste is `0.85 * mean matching genre preference + 0.15 *
media-type preference`. For a shared candidate, profiles remain separate:

```
joint taste = mean(active actor tastes)
              - 0.25 * abs(vova taste - sasha taste)  # if both have evidence
              - 0.45 * abs(most negative taste)
```

Thus a positive signal cannot simply cancel the other person's negative fit.
If only one actor has evidence, that profile drives taste without inventing a
preference for the other. With no reactions, ranking falls back explicitly to
catalogue quality and popularity. One or two reactions remain heavily shrunk.
Want-list genres are not treated as likes.

## Discovery, exclusion and ranking

Discovery fetches a bounded one-to-three pages per requested media type, applies
provider-supported constraints, and deduplicates by media type plus TMDB ID.
Genre IDs can be sent to TMDB; localized stored genre names remain local ranking
features. Discovery and ranking are separate interfaces.

All watched records are excluded, first by provider + media type + external ID,
then normalized title + year + media type. Want records use the same exclusion
by default; `exclude_want=False` is the future reusable want-list ranking mode.
No title-only exclusion is performed. Invalid/incomplete candidates are ignored.

The default quality floor is 100 votes. The deterministic total is:

```
0.70 * taste + 0.22 * quality + 0.08 * popularity
quality = 0.70 * normalized rating + 0.30 * log-normalized vote count
```

Rating is consequently a prior/tie-breaker, not the primary personalization
signal. Structured scores expose taste, genre/media fit, quality, popularity,
joint penalties, actor scores and evidence-backed Russian reasons. They never
promise that a title will be liked. Stable title and external-ID keys break
ties. A final deterministic pass applies only a small `0.04` repeated-primary-
genre adjustment, allowing a close alternative into the top results without
overriding materially stronger relevance. Franchise diversity is deferred
because discover responses do not expose collections reliably.

## Constraints, failures, cache and privacy

`RecommendationConstraints` supports movie/TV/any, included/excluded genres,
year bounds, minimum rating, maximum runtime, language, country, result limit,
vote floor, and want-list exclusion. `mood_tags` is a reserved contract and is
not interpreted. Filtering is repeated locally against normalized candidates.

The TMDB discovery adapter uses a process-local bounded 64-entry, five-minute
TTL cache. Restart invalidates it. Missing configuration returns a clean
recommendation-unavailable error; timeout, 429, other HTTP failures, invalid
JSON, and malformed payloads do not retry. Existing Films handlers are not
wired to this service, so catalogue failures cannot break navigation.

Only discovery constraints and pagination go to TMDB. Film history, reactions,
profiles and local titles are never transmitted to TMDB or Polza. No raw API
responses or actor/title reaction pairs are logged.

## Known limitations and next phase

Discover responses expose TMDB numeric genre identities while historical film
metadata stores localized names. A later catalogue genre-map step can improve
targeted discovery; local ranking already works when normalized identities
match. TV runtime may be unavailable until details are fetched. Era/language/
country taste learning and franchise detection are intentionally deferred.

The future Telegram/NL layer should select `actor` explicitly (`vova`, `sasha`,
or `both`), construct `RecommendationConstraints`, and call
`MovieRecommendationService.recommend`. It can render `CandidateScore` reasons
and later offer want-list actions. This foundation adds no button, intent,
carousel, natural-language parsing, or persistence.
