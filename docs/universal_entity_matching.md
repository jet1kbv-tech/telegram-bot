# Universal entity matching foundation

## Audit

NL mutations use the shared `resolve_entities` path for purchases, films,
personal calendar events, and Afisha events. It already retained owner
isolation, excluded Afisha calendar projections, excluded past calendar events
by default, and sorted calendar candidates chronologically. Purchases search
both existing status buckets. Films compare the display, localized, and
original titles. Tickets, places, leisure, and wishlist currently have no NL
update/delete resolver; ticket-to-Afisha association uses stored IDs rather
than title matching.

Before this patch the shared resolver applied NFKC, case folding, whitespace
collapse, and Russian `ё` to `е`, but also replaced every punctuation mark with
spaces. Thus casing and whitespace were already handled centrally, display
titles were separate from keys, and ambiguity was preserved, but punctuation
could create unsafe equivalences. Read-only calendar/Afisha target filtering
uses the same normalization primitive with its existing substring semantics.

## Matching contract

`normalize_for_match(text)` accepts and returns strings. It applies NFKC
Unicode normalization, Unicode `casefold()`, maps Russian `ё` to `е`, strips
outer whitespace, and collapses internal whitespace (the final two effects are
implemented by splitting and joining). It does not mutate stored values.

The `ё`/`е` mapping is retained because Russian users commonly omit the dots and
the previous production resolver already treated the letters as equivalent.
Punctuation, including hyphens and typographic quotes, is preserved. No
punctuation is stripped because there is no demonstrated domain-safe rule;
therefore `Spider Man` and `Spider-Man` remain different.

Resolution remains normalized exact equality. It adds no substring, fuzzy,
stemming, token, embedding, or LLM matching. Every equal candidate is returned
in existing order, so legacy records whose keys collide remain explicitly
ambiguous. Stored titles remain authoritative for UI, proposals,
confirmations, queries, and persistence.

## Morphology and future reuse

The parser prompt asks the model to tolerate Russian inflection and describes
`target` as a title, but it provides no deterministic nominative-form decoder
or stemming step. Prompt examples include inflected user phrases without
asserting their decoded target value, so canonicalization such as `поездке` to
`поездка` is provider-dependent and is not guaranteed. This patch deliberately
does not solve morphology.

The helper is domain-independent and can later be reused by event-attachment
resolvers. Attachment storage, intents, provider fields, and flows are not
introduced here; a future attachment resolver must receive the canonical
target text and retain the same ambiguity and calendar-domain filters.
