import asyncio
from copy import deepcopy

from bot.handlers import film_enrichment as handler
from bot.services.film_enrichment import (
    EnrichmentDisposition,
    apply_metadata_atomic,
    classify_search_results,
)
from bot.services.movie_metadata import MediaSearchResults, MovieMetadata, MovieMetadataUnavailable, MovieSearchResult


def result(external_id="1", title="Оно", original_title="It", year=2017, media_type="movie"):
    return MovieSearchResult("tmdb", external_id, title, original_title, year, media_type=media_type)


def metadata(external_id="1", title="Оно", description="Русское описание", year=2017, media_type="movie"):
    return MovieMetadata("tmdb", external_id, title, "It", year, ("Ужасы",), description, 7.2, media_type)


def film(**changes):
    value = {
        "id": "legacy", "title": "Оно", "status": "watched", "added_by": "user",
        "comment": "note", "sasha_rating": 8, "vova_rating": 7, "legacy_rating": 6,
        "rating": 6, "metadata_provider": "", "external_id": "", "localized_title": "",
        "media_type": "", "original_title": "", "year": None, "genres": [], "description": "", "external_rating": None,
    }
    value.update(changes)
    return value


class Store:
    def __init__(self, films):
        self.data = {"films": films}

    def load(self):
        return self.data

    def update(self, mutator):
        return mutator(self.data), self.data


def test_exact_localized_and_original_title_are_automatic():
    assert classify_search_results(film(), [result()]).disposition is EnrichmentDisposition.AUTOMATIC
    english = film(title="It")
    assert classify_search_results(english, [result()]).disposition is EnrichmentDisposition.AUTOMATIC


def test_multiple_exact_matches_and_multiple_years_are_ambiguous():
    decision = classify_search_results(film(), [result("1", year=1990), result("2", year=2017)])
    assert decision.disposition is EnrichmentDisposition.AMBIGUOUS
    assert len(decision.candidates) == 2


def test_movie_and_tv_exact_matches_are_distinct_and_ambiguous():
    decision = classify_search_results(film(), [result("1", media_type="movie"), result("1", media_type="tv")])
    assert decision.disposition is EnrichmentDisposition.AMBIGUOUS
    assert {item.media_type for item in decision.candidates} == {"movie", "tv"}


def test_incomplete_unique_exact_result_is_not_automatic():
    results = MediaSearchResults([result(media_type="tv")], complete=False)
    assert classify_search_results(film(), results).disposition is EnrichmentDisposition.AMBIGUOUS


def test_known_year_safely_disambiguates():
    decision = classify_search_results(film(year=2017), [result("1", year=1990), result("2", year=2017)])
    assert decision.disposition is EnrichmentDisposition.AUTOMATIC
    assert decision.candidates[0].external_id == "2"


def test_no_results_are_unmatched():
    assert classify_search_results(film(), []).disposition is EnrichmentDisposition.UNMATCHED


def test_atomic_apply_preserves_user_data_order_and_length():
    before = film()
    other = film(id="other", title="Другой")
    store = Store([before, other])
    protected = {key: deepcopy(before[key]) for key in (
        "id", "title", "status", "added_by", "comment", "sasha_rating", "vova_rating", "legacy_rating", "rating"
    )}

    applied = apply_metadata_atomic(store, "legacy", metadata())

    assert applied.disposition is EnrichmentDisposition.ENRICHED
    assert len(store.data["films"]) == 2
    assert [item["id"] for item in store.data["films"]] == ["legacy", "other"]
    assert {key: before[key] for key in protected} == protected
    assert before["localized_title"] == "Оно"
    assert before["description"] == "Русское описание"


def test_missing_description_remains_empty():
    target = film()
    apply_metadata_atomic(Store([target]), "legacy", metadata(description=""))
    assert target["description"] == ""


def test_already_and_partially_enriched_are_not_overwritten():
    complete = film(metadata_provider="tmdb", external_id="old")
    partial = film(id="partial", metadata_provider="tmdb")
    store = Store([complete, partial])
    assert apply_metadata_atomic(store, "legacy", metadata()).disposition is EnrichmentDisposition.SKIPPED
    assert apply_metadata_atomic(store, "partial", metadata()).disposition is EnrichmentDisposition.CONFLICT
    assert partial["external_id"] == ""


def test_external_identity_conflict_is_blocked():
    target = film()
    existing = film(id="existing", metadata_provider="tmdb", external_id="1")
    store = Store([target, existing])
    applied = apply_metadata_atomic(store, "legacy", metadata())
    assert applied.disposition is EnrichmentDisposition.CONFLICT
    assert target["external_id"] == ""
    assert len(store.data["films"]) == 2


def test_same_external_id_different_media_type_does_not_conflict():
    target = film()
    existing = film(id="existing", metadata_provider="tmdb", media_type="movie", external_id="1")
    applied = apply_metadata_atomic(Store([target, existing]), "legacy", metadata(media_type="tv"))
    assert applied.disposition is EnrichmentDisposition.ENRICHED
    assert target["media_type"] == "tv"


def test_tv_metadata_is_persisted_by_enrichment():
    target = film(title="Во все тяжкие")
    applied = apply_metadata_atomic(Store([target]), "legacy", metadata(title="Во все тяжкие", media_type="tv"))
    assert applied.disposition is EnrichmentDisposition.ENRICHED
    assert target["media_type"] == "tv"


def test_deleted_enriched_during_review_and_double_confirmation_are_safe():
    assert apply_metadata_atomic(Store([]), "legacy", metadata()).disposition is EnrichmentDisposition.DELETED
    target = film()
    store = Store([target])
    assert apply_metadata_atomic(store, "legacy", metadata()).disposition is EnrichmentDisposition.ENRICHED
    assert apply_metadata_atomic(store, "legacy", metadata()).disposition is EnrichmentDisposition.SKIPPED


class Provider:
    def __init__(self, failures=()):
        self.failures = set(failures)

    async def search_titles(self, query):
        if query in self.failures:
            raise MovieMetadataUnavailable("timeout")
        if query == "Нет":
            return []
        return [result(external_id=query, title=query, original_title="")]

    async def get_title_details(self, media_type, external_id):
        return MovieMetadata("tmdb", external_id, external_id, year=None, description="Описание", media_type=media_type)


def test_provider_failure_does_not_abort_batch_and_counters_are_correct(monkeypatch):
    store = Store([film(id="a", title="A"), film(id="b", title="Fail"), film(id="c", title="Нет")])
    monkeypatch.setattr(handler, "storage", store)
    report = asyncio.run(handler.process_enrichment_batch(Provider({"Fail"}), pace_seconds=0))
    assert report.processed == 3
    assert report.enriched == 1
    assert len(report.provider_error) == 1
    assert len(report.unmatched) == 1
    assert len(store.data["films"]) == 3


def test_rerun_skips_successfully_enriched_films(monkeypatch):
    store = Store([film(id="a", title="A")])
    monkeypatch.setattr(handler, "storage", store)
    first = asyncio.run(handler.process_enrichment_batch(Provider(), pace_seconds=0))
    second = asyncio.run(handler.process_enrichment_batch(Provider(), pace_seconds=0))
    assert first.enriched == 1
    assert second.total == 0


def test_manual_query_is_not_stored_as_title(monkeypatch):
    target = film(title="Властелин колец 1")
    store = Store([target])
    monkeypatch.setattr(handler, "storage", store)
    apply_metadata_atomic(store, "legacy", metadata(title="Властелин колец: Братство Кольца"))
    assert target["id"] == "legacy"
    assert target["title"] == "Властелин колец 1"


def test_concurrent_batch_claim_is_guarded():
    handler.release_batch()
    try:
        assert handler.claim_batch() is True
        assert handler.claim_batch() is False
        assert handler.batch_is_running() is True
    finally:
        handler.release_batch()
