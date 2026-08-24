"""Project master-ASR wording onto Aiesa's diarized turn structure.

Alignment v2.2 keeps Aiesa's turns, speakers and timestamps authoritative.  A
global ``SequenceMatcher`` is used only to estimate each Aiesa boundary.  The
actual boundary is selected independently in a bounded local window using the
suffix of the preceding Aiesa turn and prefix of the following turn.  Thus this
is not a global segmentation optimizer and a distant attractive match cannot
move a boundary through neighbouring speech.

For a candidate ``k`` the score is ``4*left_fit + 4*right_fit - .22*distance
+ syntax_adjustment + short_turn_adjustment``.  Lexical fits are SequenceMatcher
ratios over up to eight words (the complete turn when it has at most five).
Syntax adjustment is +.12 after sentence punctuation, +.04 after ``;:`` and
-.10 for no punctuation (-.04 after a comma).  The search radius is
``min(8, 2 + ceil(sqrt(shorter adjacent turn)))``.  Accepted boundaries slice
the one master token stream, which provides exact, ordered token conservation.

LOW is a local boundary-quality result, not a judgement about master wording.
Such a boundary uses the global matcher's estimate, clamped to
``[previous + 1, master_count - remaining_turns]``.  The lower bound preserves
the preceding turn and the upper bound reserves one token for every remaining
Aiesa turn.  HIGH/MEDIUM boundaries retain their locally scored positions.
Only a failure of the global or final structural guards rejects the hybrid.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from enum import Enum

from bot.services.transcript_processing import TranscriptTurn
from bot.services.polza_master_transcription import MasterTranscriptionResult

WORD_RE = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
MIN_GLOBAL_SIMILARITY = 0.45
MAX_SEARCH_RADIUS = 8
LOCAL_CONTEXT_TOKENS = 8
SHORT_TURN_TOKENS = 5
MAX_TURN_EXPANSION_RATIO = 1.6
MAX_TURN_EXPANSION_SLACK = 8


class BoundaryConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class BoundaryDecision:
    """Text-free structural diagnostics for one Aiesa boundary."""
    estimated: int
    selected: int
    confidence: BoundaryConfidence
    left_fit_bucket: str = "none"
    right_fit_bucket: str = "none"
    margin_bucket: str = "none"
    short_turn_protected: bool = False
    reason: str | None = None
    local_fallback_applied: bool = False


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

    @property
    def unsafe_boundary_count(self) -> int:
        return sum(d.confidence is BoundaryConfidence.LOW for d in self.boundaries)

    @property
    def local_fallback_boundary_count(self) -> int:
        return sum(d.local_fallback_applied for d in self.boundaries)


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
    return [_Token(m.group(0).casefold(), m.start(), m.end()) for m in WORD_RE.finditer(text)]


def _fit(source: list[str], master: list[str]) -> float:
    if not source or not master:
        return 0.0
    return SequenceMatcher(None, source, master, autojunk=False).ratio()


def _context_size(turn: list[_Token]) -> int:
    return len(turn) if len(turn) <= SHORT_TURN_TOKENS else min(LOCAL_CONTEXT_TOKENS, len(turn))


def _syntax_adjustment(text: str, tokens: list[_Token], boundary: int) -> float:
    left = tokens[boundary - 1].end if boundary else 0
    right = tokens[boundary].start if boundary < len(tokens) else len(text)
    gap = text[left:right]
    if re.search(r"[.?!]", gap):
        return 0.12
    if re.search(r"[;:]", gap):
        return 0.04
    if "," in gap:
        return -0.04
    return -0.10


def _estimate_boundary(a_boundary: int, a_count: int, g_count: int,
                       matcher: SequenceMatcher) -> tuple[int, bool, bool]:
    pairs = [(ai + offset, gi + offset) for ai, gi, size in matcher.get_matching_blocks()
             for offset in range(size)]
    left = max((p for p in pairs if p[0] < a_boundary), default=None, key=lambda x: x[0])
    right = min((p for p in pairs if p[0] >= a_boundary), default=None, key=lambda x: x[0])
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


def _bucket(value: float, medium: float, high: float) -> str:
    return "high" if value >= high else "medium" if value >= medium else "low"


def align_transcript(turns: list[TranscriptTurn], master_text: str) -> HybridAlignmentResult:
    """Select locally anchored boundaries, then conservatively accept or fall back."""
    if not turns or not master_text.strip():
        return HybridAlignmentResult(False, tuple(turns), 0.0, (), "empty_input")
    per_turn = [_tokens(turn.text) for turn in turns]
    a_words = [t.normalized for group in per_turn for t in group]
    g_tokens = _tokens(master_text)
    g_words = [t.normalized for t in g_tokens]
    if not a_words or not g_words:
        return HybridAlignmentResult(False, tuple(turns), 0.0, (), "empty_tokens")
    matcher = SequenceMatcher(None, a_words, g_words, autojunk=False)
    similarity = matcher.ratio()
    if similarity < MIN_GLOBAL_SIMILARITY:
        return HybridAlignmentResult(False, tuple(turns), similarity, (), "global_similarity")

    decisions: list[BoundaryDecision] = []
    cumulative = previous = 0
    for index in range(len(turns) - 1):
        cumulative += len(per_turn[index])
        estimate, has_left, has_right = _estimate_boundary(cumulative, len(a_words), len(g_words), matcher)
        adjacent = min(len(per_turn[index]), len(per_turn[index + 1]))
        radius = min(MAX_SEARCH_RADIUS, 2 + math.ceil(math.sqrt(max(1, adjacent))))
        # In addition to keeping this boundary non-empty, reserve one master
        # token for each source turn still to be emitted.  This makes short
        # turns an explicit invariant rather than relying on a later check.
        safe_low = previous + 1
        remaining_turns = len(turns) - index - 1
        safe_high = len(g_words) - remaining_turns
        low, high = max(safe_low, estimate - radius), min(safe_high, estimate + radius)
        if low > high:
            return HybridAlignmentResult(False, tuple(turns), similarity, tuple(decisions), "non_monotonic")

        left_words = [t.normalized for t in per_turn[index]]
        right_words = [t.normalized for t in per_turn[index + 1]]
        left_n, right_n = _context_size(per_turn[index]), _context_size(per_turn[index + 1])
        short_protected = len(left_words) <= SHORT_TURN_TOKENS or len(right_words) <= SHORT_TURN_TOKENS
        candidates = []
        for candidate in range(low, high + 1):
            left_fit = _fit(left_words[-left_n:], g_words[max(0, candidate - left_n):candidate])
            right_fit = _fit(right_words[:right_n], g_words[candidate:candidate + right_n])
            score = 4.0 * left_fit + 4.0 * right_fit - 0.22 * abs(candidate - estimate)
            score += _syntax_adjustment(master_text, g_tokens, candidate)
            # Complete short turns already drive the lexical terms.  This small
            # deterministic term makes credible preservation win close ties,
            # without manufacturing a match where none exists.
            if short_protected and max(left_fit, right_fit) >= 0.50:
                score += 0.35
            candidates.append((score, -abs(candidate - estimate), -candidate,
                               candidate, left_fit, right_fit))
        candidates.sort(reverse=True)
        best = candidates[0]
        selected, left_fit, right_fit = best[3], best[4], best[5]
        second_score = candidates[1][0] if len(candidates) > 1 else best[0] - 1.0
        margin = best[0] - second_score
        distance = abs(selected - estimate)
        lexical_floor = min(left_fit, right_fit)
        lexical_total = left_fit + right_fit
        # A provider may insert a whole clause immediately on one side of an
        # otherwise exact anchor.  Both sides remain scored, but one exact side
        # is sufficient when the global matcher confirms anchors on both sides.
        if (lexical_total >= 0.95 and max(left_fit, right_fit) >= 0.70 and
                margin >= 0.18 and distance <= 3 and has_left and has_right):
            confidence = BoundaryConfidence.HIGH
        elif (lexical_total >= 0.70 and max(left_fit, right_fit) >= 0.50 and
              margin >= 0.08 and distance <= 5 and (has_left or has_right)):
            confidence = BoundaryConfidence.MEDIUM
        else:
            confidence = BoundaryConfidence.LOW
        reason = None if confidence is not BoundaryConfidence.LOW else (
            "insufficient_lexical_fit" if lexical_total < 0.70 else
            "ambiguous_candidates" if margin < 0.08 else "excessive_displacement")
        local_fallback = confidence is BoundaryConfidence.LOW
        if local_fallback:
            # Deliberately ignore the linguistically best LOW candidate.  The
            # global Aiesa->master estimate is the conservative structural
            # answer, clamped only to the feasible monotonic partition.
            selected = max(safe_low, min(estimate, safe_high))
        decisions.append(BoundaryDecision(
            estimate, selected, confidence, _bucket(left_fit, .35, .70),
            _bucket(right_fit, .35, .70), _bucket(margin, .08, .18),
            short_protected and max(left_fit, right_fit) >= .50, reason,
            local_fallback))
        previous = selected

    boundaries = [0, *(d.selected for d in decisions), len(g_tokens)]
    if any(left >= right for left, right in zip(boundaries, boundaries[1:])):
        return HybridAlignmentResult(False, tuple(turns), similarity, tuple(decisions), "empty_turn")
    for index, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
        source_tokens, hybrid_tokens = len(per_turn[index]), right - left
        token_limit = source_tokens * MAX_TURN_EXPANSION_RATIO + MAX_TURN_EXPANSION_SLACK
        if hybrid_tokens > token_limit:
            diagnostic = PathologicalTurnDiagnostic(index, source_tokens, hybrid_tokens,
                hybrid_tokens / max(1, source_tokens), MAX_TURN_EXPANSION_RATIO,
                MAX_TURN_EXPANSION_SLACK, token_limit)
            return HybridAlignmentResult(False, tuple(turns), similarity, tuple(decisions),
                                         "pathological_turn", diagnostic)

    output = []
    for index, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
        char_start = 0 if left == 0 else g_tokens[left].start
        char_end = len(master_text) if right == len(g_tokens) else g_tokens[right].start
        output.append(replace(turns[index], text=master_text[char_start:char_end].strip()))
    output_words = [t.normalized for turn in output for t in _tokens(turn.text)]
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
