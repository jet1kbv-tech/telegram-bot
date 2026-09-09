"""Persistent, actor-scoped pointers to the last resolved context subject."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

CONTEXT_SESSION_TTL = timedelta(minutes=30)
ACTORS = frozenset({"vova", "sasha"})
DOMAINS = frozenset({"event", "trip"})


@dataclass(frozen=True, slots=True)
class ContextSession:
    domain: str
    context_id: str
    established_at: datetime
    expires_at: datetime


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def normalize_context_sessions(raw: Any) -> dict[str, dict[str, str]]:
    """Accept only the tiny, versionless pointer schema and fail closed."""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for actor, value in raw.items():
        if actor not in ACTORS or not isinstance(value, dict) or set(value) != {
                "domain", "context_id", "established_at", "expires_at"}:
            continue
        domain, context_id = value.get("domain"), value.get("context_id")
        try:
            established = datetime.fromisoformat(value["established_at"])
            expires = datetime.fromisoformat(value["expires_at"])
        except (TypeError, ValueError):
            continue
        if (domain not in DOMAINS or not isinstance(context_id, str) or not context_id
                or established.tzinfo is None or expires.tzinfo is None or expires <= established
                or expires - established > CONTEXT_SESSION_TTL):
            continue
        result[actor] = {"domain": domain, "context_id": context_id,
                         "established_at": established.isoformat(), "expires_at": expires.isoformat()}
    return result


def get_context_session(data: dict[str, Any], actor: str, now: datetime) -> ContextSession | None:
    if actor not in ACTORS:
        return None
    sessions = data.setdefault("meta", {}).setdefault("context_sessions", {})
    raw = sessions.get(actor)
    normalized = normalize_context_sessions({actor: raw}).get(actor)
    if normalized is None:
        sessions.pop(actor, None)
        return None
    session = ContextSession(normalized["domain"], normalized["context_id"],
                             datetime.fromisoformat(normalized["established_at"]),
                             datetime.fromisoformat(normalized["expires_at"]))
    if session.established_at > _utc(now) or session.expires_at <= _utc(now):
        sessions.pop(actor, None)
        return None
    return session


def set_context_session(data: dict[str, Any], actor: str, domain: str, context_id: str,
                        now: datetime) -> ContextSession:
    if actor not in ACTORS or domain not in DOMAINS or not context_id:
        raise ValueError("invalid context session")
    established = _utc(now)
    session = ContextSession(domain, context_id, established, established + CONTEXT_SESSION_TTL)
    data.setdefault("meta", {}).setdefault("context_sessions", {})[actor] = {
        "domain": session.domain, "context_id": session.context_id,
        "established_at": session.established_at.isoformat(), "expires_at": session.expires_at.isoformat(),
    }
    return session


def clear_context_session(data: dict[str, Any], actor: str) -> None:
    data.setdefault("meta", {}).setdefault("context_sessions", {}).pop(actor, None)
