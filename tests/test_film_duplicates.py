from bot.services.film_duplicates import DuplicateKind, DuplicateReason, find_movie_duplicate, normalize_movie_title
from bot.services.movie_metadata import MovieMetadata


def candidate(**changes) -> MovieMetadata:
    values = {"metadata_provider": "tmdb", "external_id": "42", "title": "Игры разума", "year": 2001}
    values.update(changes)
    return MovieMetadata(**values)


def film(**changes) -> dict:
    values = {"id": "existing", "metadata_provider": "tmdb", "external_id": "42", "title": "Игры разума", "year": 2001, "status": "want"}
    values.update(changes)
    return values


def test_exact_external_identity_is_definitive_including_legacy_provider() -> None:
    result = find_movie_duplicate(candidate(), [film(metadata_provider="")])
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
