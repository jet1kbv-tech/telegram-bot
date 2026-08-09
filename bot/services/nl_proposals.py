from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, MutableMapping

from bot.services.nl_intent import IntentKind

PROPOSALS_KEY = "ai_proposals"
ACTIVE_KEY = "ai_active_proposal_id"


@dataclass(slots=True)
class ActionProposal:
    proposal_id: str
    intent: IntentKind
    arguments: dict[str, Any]
    actor_key: str
    created_at: datetime
    expires_at: datetime
    missing_fields: list[str] = field(default_factory=list)
    clarification_count: int = 0
    status: str = "pending"


def create_proposal(user_data: MutableMapping[str, Any], *, intent: IntentKind, arguments: dict[str, Any], actor_key: str,
                    now: datetime, ttl_seconds: int, missing_fields: list[str] | None = None) -> ActionProposal:
    proposal_id = secrets.token_urlsafe(8)
    proposal = ActionProposal(proposal_id, intent, arguments, actor_key, now, now + timedelta(seconds=ttl_seconds), missing_fields or [])
    user_data[PROPOSALS_KEY] = {proposal_id: proposal}
    user_data[ACTIVE_KEY] = proposal_id
    return proposal


def get_proposal(user_data: MutableMapping[str, Any], proposal_id: str, *, actor_key: str, now: datetime) -> ActionProposal | None:
    proposals = user_data.get(PROPOSALS_KEY)
    proposal = proposals.get(proposal_id) if isinstance(proposals, dict) else None
    if not isinstance(proposal, ActionProposal) or proposal.actor_key != actor_key or proposal.expires_at <= now:
        if isinstance(proposals, dict) and isinstance(proposal, ActionProposal) and proposal.expires_at <= now:
            proposals.pop(proposal_id, None)
        return None
    return proposal


def active_proposal(user_data: MutableMapping[str, Any], *, actor_key: str, now: datetime) -> ActionProposal | None:
    proposal_id = user_data.get(ACTIVE_KEY)
    return get_proposal(user_data, str(proposal_id), actor_key=actor_key, now=now) if proposal_id else None


def discard_proposal(user_data: MutableMapping[str, Any], proposal: ActionProposal) -> None:
    proposals = user_data.get(PROPOSALS_KEY)
    if isinstance(proposals, dict):
        proposals.pop(proposal.proposal_id, None)
    if user_data.get(ACTIVE_KEY) == proposal.proposal_id:
        user_data.pop(ACTIVE_KEY, None)
