from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class MovieMetadataError(Exception):
    """Base error for metadata providers."""


class MovieMetadataUnavailable(MovieMetadataError):
    """The provider cannot currently serve the request."""


class MovieMetadataNotFound(MovieMetadataError):
    """A previously returned movie no longer exists at the provider."""


@dataclass(frozen=True, slots=True)
class MovieSearchResult:
    metadata_provider: str
    external_id: str
    title: str
    original_title: str = ""
    year: int | None = None
    description: str = ""
    external_rating: float | None = None


@dataclass(frozen=True, slots=True)
class MovieMetadata:
    metadata_provider: str
    external_id: str
    title: str
    original_title: str = ""
    year: int | None = None
    genres: tuple[str, ...] = ()
    description: str = ""
    external_rating: float | None = None

    @classmethod
    def manual(cls, title: str) -> "MovieMetadata":
        return cls(metadata_provider="", external_id="", title=title)


class MovieMetadataProvider(Protocol):
    async def search_movies(self, query: str) -> list[MovieSearchResult]: ...

    async def get_movie_details(self, external_id: str) -> MovieMetadata: ...
