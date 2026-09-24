"""Actor-bound, in-memory proposal lifecycle for Universal Capture."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, MutableMapping

from bot.services.capture import CaptureClassification, NormalizedCapture


PROPOSALS_KEY = "capture_proposals"
ACTIVE_KEY = "capture_active_proposal_id"


@dataclass(slots=True)
class CaptureProposal:
    proposal_id: str
    actor_key: str
    owner_key: str
    source: NormalizedCapture
    classification: CaptureClassification
    created_at: datetime
    expires_at: datetime
    selected_destination: str | None = None
    status: str = "pending"


def create_capture_proposal(user_data: MutableMapping[str, Any], *, actor_key: str,
                            owner_key: str, source: NormalizedCapture,
                            classification: CaptureClassification, now: datetime,
                            ttl_seconds: int) -> CaptureProposal:
    proposal_id = secrets.token_urlsafe(8)
    proposal = CaptureProposal(proposal_id, actor_key, owner_key, source, classification,
                               now, now + timedelta(seconds=ttl_seconds))
    user_data[PROPOSALS_KEY] = {proposal_id: proposal}
    user_data[ACTIVE_KEY] = proposal_id
    return proposal


def get_capture_proposal(user_data: MutableMapping[str, Any], proposal_id: str, *,
                         actor_key: str, now: datetime) -> CaptureProposal | None:
    proposals = user_data.get(PROPOSALS_KEY)
    proposal = proposals.get(proposal_id) if isinstance(proposals, dict) else None
    if not isinstance(proposal, CaptureProposal) or proposal.actor_key != actor_key:
        return None
    if proposal.expires_at <= now:
        proposals.pop(proposal_id, None)
        if user_data.get(ACTIVE_KEY) == proposal_id:
            user_data.pop(ACTIVE_KEY, None)
        return None
    return proposal


def discard_capture_proposal(user_data: MutableMapping[str, Any], proposal: CaptureProposal) -> None:
    proposals = user_data.get(PROPOSALS_KEY)
    if isinstance(proposals, dict):
        proposals.pop(proposal.proposal_id, None)
    if user_data.get(ACTIVE_KEY) == proposal.proposal_id:
        user_data.pop(ACTIVE_KEY, None)


def clear_capture_proposals(user_data: MutableMapping[str, Any]) -> None:
    """Discard only this actor's ephemeral capture state."""
    user_data.pop(PROPOSALS_KEY, None)
    user_data.pop(ACTIVE_KEY, None)
