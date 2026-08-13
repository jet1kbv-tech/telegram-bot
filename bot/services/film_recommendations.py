"""Pure, provider-independent film recommendation domain and ranking logic."""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Protocol

from bot.services.genre_vocabulary import canonicalize_genre, canonicalize_genres, genre_display_label

REACTION_WEIGHTS = {"like": 1.0, "neutral": 0.0, "dislike": -1.0}
WANT_INTEREST_WEIGHT = 0.20
PROFILE_SHRINKAGE = 2.0
MIN_VOTE_COUNT = 100
TASTE_WEIGHT = 0.70
QUALITY_WEIGHT = 0.22
POPULARITY_WEIGHT = 0.08
JOINT_DISAGREEMENT_PENALTY = 0.25
JOINT_NEGATIVE_PENALTY = 0.45


@dataclass(frozen=True, slots=True)
class RecommendationCandidate:
    external_id: str
    media_type: Literal["movie", "tv"]
    title: str
    original_title: str = ""
    year: int | None = None
    genres: tuple[str, ...] = ()
    overview: str = ""
    external_rating: float | None = None
    vote_count: int = 0
    popularity: float = 0.0
    runtime_minutes: int | None = None
    original_language: str = ""
    origin_country: tuple[str, ...] = ()
    provider: str = "tmdb"


@dataclass(frozen=True, slots=True)
class RecommendationConstraints:
    media_type: Literal["movie", "tv", "any"] = "any"
    include_genres: frozenset[str] = frozenset()
    exclude_genres: frozenset[str] = frozenset()
    min_year: int | None = None
    max_year: int | None = None
    min_rating: float | None = None
    max_runtime: int | None = None
    language: str = ""
    country: str = ""
    mood_tags: tuple[str, ...] = ()  # Reserved: deliberately not interpreted in v1.
    limit: int = 5
    exclude_want: bool = True
    min_vote_count: int = MIN_VOTE_COUNT


@dataclass(frozen=True, slots=True)
class FeatureEvidence:
    score: float
    evidence_count: int
    weighted_sum: float


@dataclass(frozen=True, slots=True)
class FilmPreferenceProfile:
    actor_key: str
    genres: dict[str, FeatureEvidence]
    media_types: dict[str, FeatureEvidence]
    like_count: int
    neutral_count: int
    dislike_count: int
    unknown_watched_count: int
    reacted_count: int
    want_genres: dict[str, FeatureEvidence] = field(default_factory=dict)
    want_media_types: dict[str, FeatureEvidence] = field(default_factory=dict)
    want_interest_count: int = 0

    @property
    def is_cold_start(self) -> bool:
        return self.reacted_count == 0


@dataclass(frozen=True, slots=True)
class CandidateScore:
    candidate: RecommendationCandidate
    total: float
    taste_score: float
    genre_fit: float
    media_type_fit: float
    quality_prior: float
    popularity_prior: float
    penalties: tuple[str, ...] = ()
    explanation_reasons: tuple[str, ...] = ()
    actor_scores: tuple[tuple[str, float], ...] = ()


class MovieCandidateProvider(Protocol):
    async def discover_movies(self, constraints: RecommendationConstraints, *, pages: int = 1,
                              start_page: int = 1, sort_by: str = "popularity.desc") -> list[RecommendationCandidate]: ...
    async def discover_tv(self, constraints: RecommendationConstraints, *, pages: int = 1,
                          start_page: int = 1, sort_by: str = "popularity.desc") -> list[RecommendationCandidate]: ...
    async def get_details(self, media_type: str, external_id: str) -> RecommendationCandidate: ...


def _feature_map(values: dict[str, list[float]]) -> dict[str, FeatureEvidence]:
    return {
        name: FeatureEvidence(sum(weights) / (len(weights) + PROFILE_SHRINKAGE), len(weights), sum(weights))
        for name, weights in sorted(values.items())
    }


def build_film_preference_profile(films: Iterable[dict[str, Any]], actor_key: str, *, include_want: bool = True) -> FilmPreferenceProfile:
    if actor_key not in {"vova", "sasha"}:
        raise ValueError("actor_key must be vova or sasha")
    counts = {key: 0 for key in REACTION_WEIGHTS}
    unknown = 0
    genre_values: dict[str, list[float]] = {}
    type_values: dict[str, list[float]] = {}
    want_genres: dict[str, list[float]] = {}
    want_types: dict[str, list[float]] = {}
    want_count = 0
    for film in films:
        if not isinstance(film, dict):
            continue
        if film.get("status") == "want":
            # Stored ownership is the human-readable canonical author. Unknown
            # legacy authors are intentionally not guessed.
            owner = {"Вова": "vova", "Саша": "sasha", "vova": "vova", "sasha": "sasha"}.get(film.get("added_by"))
            if include_want and owner == actor_key:
                reliable_genres = film.get("genres") if isinstance(film.get("genres"), (list, tuple)) else ()
                media_type = film.get("media_type")
                if reliable_genres or media_type in {"movie", "tv"}:
                    want_count += 1
                for key in canonicalize_genres(reliable_genres):
                    want_genres.setdefault(key, []).append(WANT_INTEREST_WEIGHT)
                if media_type in {"movie", "tv"}:
                    want_types.setdefault(media_type, []).append(WANT_INTEREST_WEIGHT)
            continue
        if film.get("status") != "watched":
            continue
        reactions = film.get("reactions") if isinstance(film.get("reactions"), dict) else {}
        reaction = reactions.get(actor_key)
        if reaction not in REACTION_WEIGHTS:
            unknown += 1
            continue
        counts[reaction] += 1
        weight = REACTION_WEIGHTS[reaction]
        reliable_genres = film.get("genres", ()) if isinstance(film.get("genres"), (list, tuple)) else ()
        for key in canonicalize_genres(reliable_genres):
            genre_values.setdefault(key, []).append(weight)
        media_type = film.get("media_type")
        if media_type in {"movie", "tv"}:
            type_values.setdefault(media_type, []).append(weight)
    reacted = sum(counts.values())
    return FilmPreferenceProfile(actor_key, _feature_map(genre_values), _feature_map(type_values),
                                 counts["like"], counts["neutral"], counts["dislike"], unknown, reacted,
                                 _feature_map(want_genres), _feature_map(want_types), want_count)


def _normal(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return re.sub(r"\W+", " ", value, flags=re.UNICODE).strip()


def _candidate_taste(candidate: RecommendationCandidate, profile: FilmPreferenceProfile) -> tuple[float, float, float, list[str]]:
    genres = canonicalize_genres(candidate.genres)
    genre_evidence = [profile.genres[g] for g in genres if g in profile.genres]
    explicit_genre_fit = sum(value.score for value in genre_evidence) / len(genre_evidence) if genre_evidence else 0.0
    want_evidence = [profile.want_genres[g] for g in genres if g in profile.want_genres]
    want_genre_fit = sum(value.score for value in want_evidence) / len(want_evidence) if want_evidence else 0.0
    # Additive weak evidence cannot average away a strong explicit dislike.
    genre_fit = explicit_genre_fit + want_genre_fit
    media = profile.media_types.get(candidate.media_type)
    media_fit = media.score if media and media.evidence_count >= 3 else 0.0
    taste = genre_fit * 0.85 + media_fit * 0.15
    reasons: list[str] = []
    positives = [g for g in genres if (ev := profile.genres.get(g)) and ev.score > 0 and ev.evidence_count >= 1]
    negatives = [g for g in genres if (ev := profile.genres.get(g)) and ev.score < 0 and ev.evidence_count >= 1]
    actor_label = {"vova": "Вова", "sasha": "Саша"}[profile.actor_key]
    if positives:
        reasons.append(f"{actor_label} положительно оценивал(а) жанр: {genre_display_label(positives[0]).capitalize()}.")
    elif want_evidence:
        saved = next(g for g in genres if g in profile.want_genres)
        reasons.append(f"У {actor_label} в сохранённых встречается жанр: {genre_display_label(saved).capitalize()}.")
    if negatives:
        reasons.append(f"Есть отрицательный сигнал по жанру: {genre_display_label(negatives[0]).capitalize()}.")
    return taste, genre_fit, media_fit, reasons


def _quality(candidate: RecommendationCandidate) -> tuple[float, float]:
    rating = max(0.0, min(10.0, candidate.external_rating or 0.0)) / 10.0
    votes = min(1.0, math.log10(max(1, candidate.vote_count)) / 5.0)
    quality = rating * 0.7 + votes * 0.3
    popularity = min(1.0, math.log1p(max(0.0, candidate.popularity)) / math.log(1001))
    return quality, popularity


def _valid_candidate(candidate: Any, constraints: RecommendationConstraints) -> bool:
    if not isinstance(candidate, RecommendationCandidate) or not candidate.external_id or not candidate.title:
        return False
    if candidate.media_type not in {"movie", "tv"} or candidate.vote_count < constraints.min_vote_count:
        return False
    if constraints.media_type != "any" and candidate.media_type != constraints.media_type:
        return False
    genres = set(canonicalize_genres(candidate.genres))
    includes = {canonicalize_genre(value) or _normal(value) for value in constraints.include_genres}
    excludes = {canonicalize_genre(value) or _normal(value) for value in constraints.exclude_genres}
    if includes and not includes.issubset(genres) or excludes & genres:
        return False
    if constraints.min_year is not None and (candidate.year is None or candidate.year < constraints.min_year): return False
    if constraints.max_year is not None and (candidate.year is None or candidate.year > constraints.max_year): return False
    if constraints.min_rating is not None and (candidate.external_rating is None or candidate.external_rating < constraints.min_rating): return False
    if constraints.max_runtime is not None and (candidate.runtime_minutes is None or candidate.runtime_minutes > constraints.max_runtime): return False
    if constraints.language and candidate.original_language.casefold() != constraints.language.casefold(): return False
    if constraints.country and constraints.country.casefold() not in {c.casefold() for c in candidate.origin_country}: return False
    return True


def _identity(candidate: RecommendationCandidate) -> tuple[str, ...]:
    return (candidate.provider, candidate.media_type, candidate.external_id)


def _fallback_identity(item: dict[str, Any]) -> tuple[str, int | None, str]:
    return (_normal(str(item.get("localized_title") or item.get("title") or "")), item.get("year"), str(item.get("media_type") or ""))


def filter_candidates(candidates: Iterable[Any], watched: Iterable[dict[str, Any]], want: Iterable[dict[str, Any]],
                      constraints: RecommendationConstraints) -> list[RecommendationCandidate]:
    watched = list(watched); want = list(want)
    blocked = watched + (want if constraints.exclude_want else [])
    external = {(str(x.get("metadata_provider") or ""), str(x.get("media_type") or ""), str(x.get("external_id") or ""))
                for x in blocked if x.get("external_id")}
    fallback = {_fallback_identity(x) for x in blocked}
    result, seen = [], set()
    for candidate in candidates:
        if not _valid_candidate(candidate, constraints) or _identity(candidate) in external:
            continue
        if (_normal(candidate.title), candidate.year, candidate.media_type) in fallback:
            continue
        key = _identity(candidate)
        if key not in seen:
            seen.add(key); result.append(candidate)
    return result


def _single_score(candidate: RecommendationCandidate, profile: FilmPreferenceProfile) -> CandidateScore:
    taste, genre, media, reasons = _candidate_taste(candidate, profile)
    quality, popularity = _quality(candidate)
    if quality >= .65 and candidate.vote_count >= 1000:
        reasons.append("Высокая внешняя оценка подтверждена большим числом голосов.")
    if profile.is_cold_start:
        reasons.append("Недостаточно реакций: использован рейтинг и популярность каталога.")
    total = taste * TASTE_WEIGHT + quality * QUALITY_WEIGHT + popularity * POPULARITY_WEIGHT
    return CandidateScore(candidate, total, taste, genre, media, quality, popularity,
                          explanation_reasons=tuple(reasons), actor_scores=((profile.actor_key, taste),))


def _joint_score(candidate: RecommendationCandidate, profiles: tuple[FilmPreferenceProfile, FilmPreferenceProfile]) -> CandidateScore:
    parts = [_candidate_taste(candidate, p) for p in profiles]
    tastes = [part[0] for part in parts]
    active = [score for score, profile in zip(tastes, profiles) if profile.reacted_count or profile.want_interest_count]
    base = sum(active) / len(active) if active else 0.0
    disagreement = abs(tastes[0] - tastes[1]) * JOINT_DISAGREEMENT_PENALTY if all(p.reacted_count or p.want_interest_count for p in profiles) else 0.0
    negative = abs(min(0.0, min(tastes))) * JOINT_NEGATIVE_PENALTY
    joint_taste = base - disagreement - negative
    quality, popularity = _quality(candidate)
    reasons = [reason for part in parts for reason in part[3]]
    penalties = []
    if disagreement: penalties.append("joint disagreement penalty")
    if negative: penalties.append("one-person negative-fit penalty")
    if not active: reasons.append("У обоих пока недостаточно реакций: использован рейтинг и популярность каталога.")
    total = joint_taste * TASTE_WEIGHT + quality * QUALITY_WEIGHT + popularity * POPULARITY_WEIGHT
    return CandidateScore(candidate, total, joint_taste, sum(p[1] for p in parts) / 2, sum(p[2] for p in parts) / 2,
                          quality, popularity, tuple(penalties), tuple(reasons),
                          tuple((profile.actor_key, taste) for profile, taste in zip(profiles, tastes)))


_SEQUEL_WORDS = {"part", "часть", "chapter", "глава", "volume", "том"}
_NUMERAL = re.compile(r"^(?:\d+|[ivxlcdm]+|one|two|three|four|five|первая|вторая|трет(?:ья|ий))$")


def title_family(title: str) -> str:
    """Return a conservative family only when a sequel marker can be removed."""
    tokens = _normal(re.sub(r"\(?(?:19|20)\d{2}\)?", " ", title)).split()
    original = tuple(tokens)
    while tokens and _NUMERAL.match(tokens[-1]):
        tokens.pop()
    if tokens and tokens[-1] in _SEQUEL_WORDS:
        tokens.pop()
    # Subtitle sequel markers ("Dune: Part Two") are handled by trimming from
    # the marker; ordinary subtitles remain distinct.
    for index, token in enumerate(tokens):
        if token in _SEQUEL_WORDS and index > 0:
            tokens = tokens[:index]
            break
    if tuple(tokens) == original or not tokens or len("".join(tokens)) < 4:
        return ""
    return " ".join(tokens)


def _related(left: RecommendationCandidate, right: RecommendationCandidate) -> bool:
    lf, rf = title_family(left.title), title_family(right.title)
    if lf and (lf == rf or lf == _normal(right.title)):
        return True
    return bool(rf and rf == _normal(left.title))


def _diversify(scores: list[CandidateScore], limit: int, *, allow_related: bool = False) -> list[CandidateScore]:
    selected: list[CandidateScore] = []
    remaining = scores[:]
    while remaining and len(selected) < limit:
        best_index = 0
        for index, score in enumerate(remaining):
            primary = _normal(score.candidate.genres[0]) if score.candidate.genres else ""
            repeated = sum(bool(primary and s.candidate.genres and _normal(s.candidate.genres[0]) == primary) for s in selected)
            related = any(_related(score.candidate, s.candidate) for s in selected)
            adjusted = score.total - repeated * 0.04 - (0.20 if related and not allow_related else 0.0)
            current = remaining[best_index]
            current_primary = _normal(current.candidate.genres[0]) if current.candidate.genres else ""
            current_repeated = sum(bool(current_primary and s.candidate.genres and _normal(s.candidate.genres[0]) == current_primary) for s in selected)
            current_related = any(_related(current.candidate, s.candidate) for s in selected)
            current_adjusted = current.total - current_repeated * .04 - (0.20 if current_related and not allow_related else 0.0)
            if (adjusted, score.candidate.title.casefold(), score.candidate.external_id) > (current_adjusted, current.candidate.title.casefold(), current.candidate.external_id):
                best_index = index
        selected.append(remaining.pop(best_index))
    return selected


def rank_candidates(candidates: Iterable[Any], profile: FilmPreferenceProfile | tuple[FilmPreferenceProfile, FilmPreferenceProfile],
                    watched: Iterable[dict[str, Any]] = (), want: Iterable[dict[str, Any]] = (),
                    constraints: RecommendationConstraints | None = None) -> list[CandidateScore]:
    constraints = constraints or RecommendationConstraints()
    filtered = filter_candidates(candidates, watched, want, constraints)
    scores = [_joint_score(c, profile) if isinstance(profile, tuple) else _single_score(c, profile) for c in filtered]
    scores.sort(key=lambda x: (-x.total, x.candidate.title.casefold(), x.candidate.external_id))
    return _diversify(scores, max(1, constraints.limit))


def profiles_for_actor(films: Iterable[dict[str, Any]], actor: str, *, include_want: bool = True) -> FilmPreferenceProfile | tuple[FilmPreferenceProfile, FilmPreferenceProfile]:
    films = list(films)
    if actor == "both":
        return build_film_preference_profile(films, "vova", include_want=include_want), build_film_preference_profile(films, "sasha", include_want=include_want)
    if actor not in {"vova", "sasha"}: raise ValueError("actor must be vova, sasha, or both")
    return build_film_preference_profile(films, actor, include_want=include_want)
