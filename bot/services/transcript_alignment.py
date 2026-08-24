"""Pure, deterministic assignment of master-ASR wording to Aiesa speaker turns."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from enum import Enum

from bot.services.transcript_processing import TranscriptTurn
from bot.services.polza_master_transcription import MasterTranscriptionResult

WORD_RE = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
MIN_GLOBAL_SIMILARITY = 0.45
MIN_SEARCH_RADIUS = 1
MAX_SEARCH_RADIUS = 6
ABSOLUTE_MAX_BOUNDARY_SHIFT = 8
LOCAL_CONTEXT_TOKENS = 4
MAX_UNSAFE_BOUNDARIES = 0
MAX_TURN_EXPANSION_RATIO = 1.6
MAX_TURN_EXPANSION_SLACK = 8


class BoundaryConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class BoundaryDecision:
    estimated: int
    selected: int
    confidence: BoundaryConfidence


@dataclass(frozen=True)
class PathologicalTurnDiagnostic:
    """Bounded, text-free context for explaining an expansion rejection."""
    index: int
    source_tokens: int
    hybrid_tokens: int
    ratio: float
    ratio_limit: float
    absolute_slack: int
    token_limit: float


@dataclass(frozen=True)
class HybridAlignmentResult:
    accepted: bool
    turns: tuple[TranscriptTurn, ...]
    similarity: float
    boundaries: tuple[BoundaryDecision, ...]
    rejection_reason: str | None = None
    pathological_turn: PathologicalTurnDiagnostic | None = None


@dataclass(frozen=True)
class TranscriptSelection:
    turns: tuple[TranscriptTurn, ...]
    source: str
    alignment: HybridAlignmentResult | None


@dataclass(frozen=True)
class _Token:
    normalized: str
    start: int
    end: int


def _tokens(text: str) -> list[_Token]:
    return [_Token(match.group(0).casefold(), match.start(), match.end()) for match in WORD_RE.finditer(text)]


def _punctuation_strength(text: str, tokens: list[_Token], boundary: int) -> int:
    left = tokens[boundary - 1].end if boundary else 0
    right = tokens[boundary].start if boundary < len(tokens) else len(text)
    gap = text[left:right]
    if re.search(r"[.?!]", gap):
        return 3
    if re.search(r"[;:]", gap):
        return 2
    if "," in gap:
        return 1
    return 0


def _estimate_boundary(a_boundary: int, a_count: int, g_count: int,
                       matcher: SequenceMatcher) -> tuple[int, bool, bool]:
    pairs = [(ai + offset, gi + offset) for ai, gi, size in matcher.get_matching_blocks()
             for offset in range(size)]
    left = max((pair for pair in pairs if pair[0] < a_boundary), default=None, key=lambda x: x[0])
    right = min((pair for pair in pairs if pair[0] >= a_boundary), default=None, key=lambda x: x[0])
    if left and right:
        span = right[0] - left[0]
        estimate = left[1] + round((a_boundary - left[0]) * (right[1] - left[1]) / max(1, span))
    elif left:
        estimate = left[1] + (a_boundary - left[0])
    elif right:
        estimate = right[1] - (right[0] - a_boundary)
    else:
        estimate = round(a_boundary * g_count / max(1, a_count))
    return max(0, min(g_count, estimate)), left is not None, right is not None


def align_transcript(turns: list[TranscriptTurn], master_text: str) -> HybridAlignmentResult:
    """Align words globally, then select lexical-first bounded speaker boundaries."""
    if not turns or not master_text.strip():
        return HybridAlignmentResult(False, tuple(turns), 0.0, (), "empty_input")
    per_turn = [_tokens(turn.text) for turn in turns]
    a_words = [token.normalized for group in per_turn for token in group]
    g_tokens = _tokens(master_text)
    g_words = [token.normalized for token in g_tokens]
    if not a_words or not g_words:
        return HybridAlignmentResult(False, tuple(turns), 0.0, (), "empty_tokens")
    matcher = SequenceMatcher(None, a_words, g_words, autojunk=False)
    similarity = matcher.ratio()
    if similarity < MIN_GLOBAL_SIMILARITY:
        return HybridAlignmentResult(False, tuple(turns), similarity, (), "global_similarity")

    decisions: list[BoundaryDecision] = []
    cumulative = 0
    previous = 0
    for index in range(len(turns) - 1):
        cumulative += len(per_turn[index])
        estimate, has_left, has_right = _estimate_boundary(cumulative, len(a_words), len(g_words), matcher)
        nearby_size = min(len(per_turn[index]), len(per_turn[index + 1]))
        quality_radius = 1 if similarity >= 0.85 else 2 if similarity >= 0.65 else 3
        radius = min(MAX_SEARCH_RADIUS, ABSOLUTE_MAX_BOUNDARY_SHIFT,
                     max(MIN_SEARCH_RADIUS, min(nearby_size // 3 + quality_radius, MAX_SEARCH_RADIUS)))
        low, high = max(previous + 1, estimate - radius), min(len(g_words) - 1, estimate + radius)
        if low > high:
            return HybridAlignmentResult(False, tuple(turns), similarity, tuple(decisions), "non_monotonic")
        # Distance has a 4-point cost while punctuation is at most 3: punctuation can
        # break a local tie, but can never buy even one token of unsupported movement.
        candidates = [(abs(candidate - estimate) * -4 + _punctuation_strength(master_text, g_tokens, candidate),
                       -abs(candidate - estimate), candidate) for candidate in range(low, high + 1)]
        candidates.sort(reverse=True)
        selected = candidates[0][2]
        distance = abs(selected - estimate)
        local_left = has_left and cumulative > 0
        local_right = has_right and cumulative < len(a_words)
        if distance <= 1 and local_left and local_right:
            confidence = BoundaryConfidence.HIGH
        elif distance <= 2 and (local_left or local_right) and similarity >= 0.65:
            confidence = BoundaryConfidence.MEDIUM
        else:
            confidence = BoundaryConfidence.LOW
        decisions.append(BoundaryDecision(estimate, selected, confidence))
        previous = selected

    boundaries = [0, *(decision.selected for decision in decisions), len(g_tokens)]
    if any(left >= right for left, right in zip(boundaries, boundaries[1:])):
        return HybridAlignmentResult(False, tuple(turns), similarity, tuple(decisions), "empty_turn")
    if sum(d.confidence is BoundaryConfidence.LOW for d in decisions) > MAX_UNSAFE_BOUNDARIES:
        return HybridAlignmentResult(False, tuple(turns), similarity, tuple(decisions), "unsafe_boundary")
    for index, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
        source_tokens = len(per_turn[index])
        hybrid_tokens = right - left
        # A fixed allowance prevents a few legitimately restored ASR words from
        # dominating the ratio for short turns. The relative term still scales
        # the guard for longer turns without permitting multi-turn absorption.
        token_limit = source_tokens * MAX_TURN_EXPANSION_RATIO + MAX_TURN_EXPANSION_SLACK
        if hybrid_tokens > token_limit:
            diagnostic = PathologicalTurnDiagnostic(
                index=index,
                source_tokens=source_tokens,
                hybrid_tokens=hybrid_tokens,
                ratio=hybrid_tokens / max(1, source_tokens),
                ratio_limit=MAX_TURN_EXPANSION_RATIO,
                absolute_slack=MAX_TURN_EXPANSION_SLACK,
                token_limit=token_limit,
            )
            return HybridAlignmentResult(False, tuple(turns), similarity, tuple(decisions),
                                         "pathological_turn", diagnostic)

    output: list[TranscriptTurn] = []
    for index, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
        char_start = 0 if left == 0 else g_tokens[left].start
        char_end = len(master_text) if right == len(g_tokens) else g_tokens[right].start
        output.append(replace(turns[index], text=master_text[char_start:char_end].strip()))
    # Structural and exact token-coverage guard (including repeated words).
    output_words = [token.normalized for turn in output for token in _tokens(turn.text)]
    if output_words != g_words or len(output) != len(turns):
        return HybridAlignmentResult(False, tuple(turns), similarity, tuple(decisions), "token_coverage")
    if any((out.speaker, out.start, out.end, out.timestamp) !=
           (source.speaker, source.start, source.end, source.timestamp)
           for out, source in zip(output, turns, strict=True)):
        return HybridAlignmentResult(False, tuple(turns), similarity, tuple(decisions), "speaker_metadata")
    return HybridAlignmentResult(True, tuple(output), similarity, tuple(decisions))


def select_best_transcript(turns: list[TranscriptTurn], master: MasterTranscriptionResult | None) -> TranscriptSelection:
    """Apply the mandatory fail-safe source precedence without network/storage concerns."""
    if master is None or master.outcome != "success":
        return TranscriptSelection(tuple(turns), "aiesa_fallback", None)
    alignment = align_transcript(turns, master.text)
    return TranscriptSelection(alignment.turns if alignment.accepted else tuple(turns),
                               "hybrid" if alignment.accepted else "aiesa_fallback", alignment)
