from bot.services.film_duplicates import DuplicateKind, DuplicateReason, find_movie_duplicate, normalize_movie_title
from bot.services.movie_metadata import MovieMetadata


def candidate(**changes) -> MovieMetadata:
    values = {"metadata_provider": "tmdb", "external_id": "42", "title": "Игры разума", "year": 2001, "media_type": "movie"}
    values.update(changes)
    return MovieMetadata(**values)


def film(**changes) -> dict:
    values = {"id": "existing", "metadata_provider": "tmdb", "media_type": "movie", "external_id": "42", "title": "Игры разума", "year": 2001, "status": "want"}
    values.update(changes)
    return values


def test_exact_external_identity_is_definitive() -> None:
    result = find_movie_duplicate(candidate(), [film()])
    assert result.kind is DuplicateKind.DEFINITIVE
    assert result.reason is DuplicateReason.EXTERNAL_ID


def test_normalized_title_and_year_is_definitive() -> None:
    result = find_movie_duplicate(candidate(external_id="99"), [film(external_id="", title="  ИГРЫ —  РАЗУМА ")])
    assert result.kind is DuplicateKind.DEFINITIVE
    assert result.reason is DuplicateReason.TITLE_AND_YEAR
    assert normalize_movie_title(" Ёлки: новые! ") == "елки новые"


def test_same_title_different_known_year_is_not_duplicate() -> None:
    result = find_movie_duplicate(candidate(year=2025, external_id="99"), [film(external_id="", year=2001)])
    assert result.kind is DuplicateKind.NONE


def test_legacy_title_without_year_is_possible_duplicate() -> None:
    result = find_movie_duplicate(candidate(external_id="99"), [film(external_id="", year=None)])
    assert result.kind is DuplicateKind.POSSIBLE
    assert result.reason is DuplicateReason.TITLE_WITH_MISSING_YEAR


def test_duplicate_scan_does_not_depend_on_status() -> None:
    for status in ("want", "watched"):
        result = find_movie_duplicate(candidate(), [film(status=status)])
        assert result.kind is DuplicateKind.DEFINITIVE
        assert result.matching_film["status"] == status


def test_same_provider_id_different_known_type_is_not_duplicate() -> None:
    assert find_movie_duplicate(candidate(media_type="tv"), [film()]).kind is DuplicateKind.NONE


def test_same_title_year_different_known_type_is_not_duplicate() -> None:
    value = candidate(external_id="99", media_type="tv")
    assert find_movie_duplicate(value, [film(external_id="")]).kind is DuplicateKind.NONE


def test_unknown_type_title_year_is_only_possible() -> None:
    existing = film(metadata_provider="", external_id="", media_type="")
    assert find_movie_duplicate(candidate(external_id="99"), [existing]).kind is DuplicateKind.POSSIBLE


def test_historical_tmdb_identity_is_effectively_movie_only() -> None:
    historical = film(media_type="")
    assert find_movie_duplicate(candidate(), [historical]).kind is DuplicateKind.DEFINITIVE
    assert find_movie_duplicate(candidate(media_type="tv"), [historical]).kind is DuplicateKind.NONE
