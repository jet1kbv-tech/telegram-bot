from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.storage import (
    FILM_REACTION_ACTORS,
    FILM_REACTION_VALUES,
    delete_item_by_id,
    find_item,
    storage,
)


@dataclass(frozen=True, slots=True)
class FilmOperationResult:
    found: bool
    film: dict[str, Any] | None = None


def set_film_status(film_id: str, status: str) -> FilmOperationResult:
    """Atomically change a film status for both legacy and filtered navigation."""

    def mutator(data: dict[str, Any]) -> FilmOperationResult:
        film = find_item(data.get("films", []), film_id)
        if film is None:
            return FilmOperationResult(False)
        film["status"] = status
        return FilmOperationResult(True, dict(film))

    result, _ = storage.update(mutator)
    return result


def set_film_reaction(film_id: str, actor_key: str, reaction: str) -> FilmOperationResult:
    """Atomically set one actor's allow-listed preference on a film."""
    if actor_key not in FILM_REACTION_ACTORS:
        raise ValueError("invalid film reaction actor")
    if reaction not in FILM_REACTION_VALUES:
        raise ValueError("invalid film reaction")

    def mutator(data: dict[str, Any]) -> FilmOperationResult:
        film = find_item(data.get("films", []), film_id)
        if film is None:
            return FilmOperationResult(False)
        film.setdefault("reactions", {})[actor_key] = reaction
        return FilmOperationResult(True, dict(film))

    result, _ = storage.update(mutator)
    return result


def clear_film_reaction(film_id: str, actor_key: str) -> FilmOperationResult:
    """Atomically clear one actor's preference without touching the other."""
    if actor_key not in FILM_REACTION_ACTORS:
        raise ValueError("invalid film reaction actor")

    def mutator(data: dict[str, Any]) -> FilmOperationResult:
        film = find_item(data.get("films", []), film_id)
        if film is None:
            return FilmOperationResult(False)
        reactions = film.get("reactions")
        if isinstance(reactions, dict):
            reactions.pop(actor_key, None)
        return FilmOperationResult(True, dict(film))

    result, _ = storage.update(mutator)
    return result


def delete_film(film_id: str) -> FilmOperationResult:
    """Atomically delete a film and return its former value when it existed."""

    def mutator(data: dict[str, Any]) -> FilmOperationResult:
        films = data.get("films", [])
        film = find_item(films, film_id)
        if film is None:
            return FilmOperationResult(False)
        snapshot = dict(film)
        delete_item_by_id(films, film_id)
        return FilmOperationResult(True, snapshot)

    result, _ = storage.update(mutator)
    return result
