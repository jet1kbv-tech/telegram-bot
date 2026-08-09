from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

MEDIA_TYPE_MOVIE = "movie"
MEDIA_TYPE_TV = "tv"
MEDIA_TYPES = frozenset({MEDIA_TYPE_MOVIE, MEDIA_TYPE_TV})

class MovieMetadataError(Exception):
    """Base error for metadata providers."""


class MovieMetadataUnavailable(MovieMetadataError):
    """The provider cannot currently serve the request."""


class MovieMetadataNotFound(MovieMetadataError):
    """A previously returned movie no longer exists at the provider."""


MediaMetadataError = MovieMetadataError
MediaMetadataUnavailable = MovieMetadataUnavailable
MediaMetadataNotFound = MovieMetadataNotFound


@dataclass(frozen=True, slots=True)
class MediaSearchResult:
    metadata_provider: str
    external_id: str
    title: str
    original_title: str = ""
    year: int | None = None
    description: str = ""
    external_rating: float | None = None
    media_type: str = ""


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    metadata_provider: str
    external_id: str
    title: str
    original_title: str = ""
    year: int | None = None
    genres: tuple[str, ...] = ()
    description: str = ""
    external_rating: float | None = None
    media_type: str = ""

    @classmethod
    def manual(cls, title: str) -> "MediaMetadata":
        return cls(metadata_provider="", external_id="", title=title, media_type="")


class MediaSearchResults(list[MediaSearchResult]):
    """Combined provider results with completeness information for safe automation."""

    def __init__(self, values=(), *, complete: bool = True) -> None:
        super().__init__(values)
        self.complete = complete


class MediaMetadataProvider(Protocol):
    async def search_titles(self, query: str) -> list[MediaSearchResult]: ...

    async def get_title_details(self, media_type: str, external_id: str) -> MediaMetadata: ...


# Source compatibility for integrations and tests written before Films v2 Phase 3.5.
MovieSearchResult = MediaSearchResult
MovieMetadata = MediaMetadata
MovieMetadataProvider = MediaMetadataProvider
