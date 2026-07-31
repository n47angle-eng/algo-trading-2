"""Typed, deterministic run-evidence records for Batch 3.

The execution engine captures observations at the point where it makes a
decision.  This module turns those observations into durable, JSON-safe
evidence only after the runner has assigned the final globally ordered event
sequences.  It deliberately contains no persistence or engine side effects.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from math import isfinite
from typing import Literal, cast

type PrimitiveValue = str | int | float | bool | None
type ConditionStatus = Literal["passed", "failed", "not_evaluated"]

_MACHINE_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_TRADE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_ALLOWED_OPERATORS = frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "in", "crosses"})
_LAYER_ORDER = {"daily": 0, "mid": 1, "entry": 2, "execution": 3}


class EvidenceValidationError(ValueError):
    """Raised when a new evidence artifact cannot be published truthfully."""


class EventOrigin(StrEnum):
    """Internal namespace preventing local strategy/execution sequence collisions."""

    STRATEGY = "strategy"
    EXECUTION = "execution"


@dataclass(frozen=True, slots=True)
class EventRef:
    """One pre-resequence event identity resolved to a final sequence at export time."""

    origin: EventOrigin
    origin_sequence: int

    def __post_init__(self) -> None:
        if self.origin_sequence <= 0:
            msg = "event origin_sequence must be positive"
            raise EvidenceValidationError(msg)


@dataclass(frozen=True, slots=True)
class ConditionObservation:
    """A typed condition captured at evaluation time before event resequencing."""

    condition_id: str
    layer_id: str
    observed_at: datetime
    status: ConditionStatus
    actual: PrimitiveValue
    operator: str
    required: PrimitiveValue
    unit: str

    def __post_init__(self) -> None:
        _validate_condition_shape(
            condition_id=self.condition_id,
            layer_id=self.layer_id,
            observed_at=self.observed_at,
            status=self.status,
            actual=self.actual,
            operator=self.operator,
            required=self.required,
            unit=self.unit,
        )
        object.__setattr__(self, "observed_at", _as_utc(self.observed_at, "condition observed_at"))


@dataclass(frozen=True, slots=True)
class ConditionFact:
    """Durable source-of-truth fact with final event sequence references."""

    condition_id: str
    layer_id: str
    observed_at: datetime
    status: ConditionStatus
    actual: PrimitiveValue
    operator: str
    required: PrimitiveValue
    unit: str
    source_sequences: tuple[int, ...]

    def __post_init__(self) -> None:
        _validate_condition_shape(
            condition_id=self.condition_id,
            layer_id=self.layer_id,
            observed_at=self.observed_at,
            status=self.status,
            actual=self.actual,
            operator=self.operator,
            required=self.required,
            unit=self.unit,
        )
        normalized_at = _as_utc(self.observed_at, "condition observed_at")
        sequences = _normalize_sequences(self.source_sequences)
        if self.status in {"passed", "failed"} and not sequences:
            msg = "passed/failed condition facts require at least one source sequence"
            raise EvidenceValidationError(msg)
        if self.status == "not_evaluated" and sequences:
            msg = "not_evaluated condition facts must not claim source sequences"
            raise EvidenceValidationError(msg)
        object.__setattr__(self, "observed_at", normalized_at)
        object.__setattr__(self, "source_sequences", sequences)

    @classmethod
    def from_observation(
        cls,
        observation: ConditionObservation,
        *,
        source_sequences: Sequence[int],
    ) -> ConditionFact:
        """Resolve one runtime observation without changing its native primitive values."""
        resolved_sequences = (
            () if observation.status == "not_evaluated" else tuple(source_sequences)
        )
        return cls(
            condition_id=observation.condition_id,
            layer_id=observation.layer_id,
            observed_at=observation.observed_at,
            status=observation.status,
            actual=observation.actual,
            operator=observation.operator,
            required=observation.required,
            unit=observation.unit,
            source_sequences=resolved_sequences,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a stable primitive-only artifact shape."""
        return {
            "condition_id": self.condition_id,
            "layer_id": self.layer_id,
            "observed_at": _iso(self.observed_at),
            "status": self.status,
            "actual": self.actual,
            "operator": self.operator,
            "required": self.required,
            "unit": self.unit,
            "source_sequences": list(self.source_sequences),
        }


@dataclass(frozen=True, slots=True)
class SignalEvaluationContext:
    """Typed signal-side context retained with every rejected evaluation."""

    candidate_signal_kinds: tuple[str, ...]
    inside_count: int | None
    entry_pullback_state: str
    mid_pullback_state: str
    daily_regime: str | None

    def __post_init__(self) -> None:
        if not self.candidate_signal_kinds or any(
            not value for value in self.candidate_signal_kinds
        ):
            msg = "signal evaluation context requires candidate signal kinds"
            raise EvidenceValidationError(msg)
        if self.inside_count is not None and self.inside_count <= 0:
            msg = "inside_count must be positive when supplied"
            raise EvidenceValidationError(msg)
        if not self.entry_pullback_state or not self.mid_pullback_state:
            msg = "signal evaluation context requires pullback states"
            raise EvidenceValidationError(msg)

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_signal_kinds": list(self.candidate_signal_kinds),
            "inside_count": self.inside_count,
            "entry_pullback_state": self.entry_pullback_state,
            "mid_pullback_state": self.mid_pullback_state,
            "daily_regime": self.daily_regime,
        }


@dataclass(frozen=True, slots=True)
class RejectionCapture:
    """Runtime capture attached internally to one ``signal_rejected`` event."""

    evidence_id: str
    evaluation_sequence: int
    reached_layers: tuple[str, ...]
    condition_observations: tuple[ConditionObservation, ...]
    blocking_condition_ids: tuple[str, ...]
    context: SignalEvaluationContext

    def __post_init__(self) -> None:
        _require_machine_id(self.evidence_id, "evidence_id")
        if self.evaluation_sequence <= 0:
            msg = "evaluation_sequence must be positive"
            raise EvidenceValidationError(msg)
        if not self.reached_layers:
            msg = "rejection evidence requires at least one reached layer"
            raise EvidenceValidationError(msg)
        if any(not _MACHINE_ID.fullmatch(layer) for layer in self.reached_layers):
            msg = "reached_layers must contain stable machine identifiers"
            raise EvidenceValidationError(msg)
        if tuple(dict.fromkeys(self.reached_layers)) != self.reached_layers:
            msg = "reached_layers must preserve each reached layer exactly once"
            raise EvidenceValidationError(msg)
        if not self.condition_observations:
            msg = "rejection evidence requires condition observations"
            raise EvidenceValidationError(msg)
        if len({fact.condition_id for fact in self.condition_observations}) != len(
            self.condition_observations
        ):
            msg = "rejection evidence cannot repeat a condition observation"
            raise EvidenceValidationError(msg)
        facts_by_id = {fact.condition_id: fact for fact in self.condition_observations}
        if not self.blocking_condition_ids:
            msg = "rejection evidence requires at least one blocking condition"
            raise EvidenceValidationError(msg)
        if tuple(sorted(set(self.blocking_condition_ids))) != self.blocking_condition_ids:
            msg = "blocking_condition_ids must be sorted and unique"
            raise EvidenceValidationError(msg)
        for condition_id in self.blocking_condition_ids:
            fact = facts_by_id.get(condition_id)
            if fact is None or fact.status != "failed":
                msg = "blocking_condition_ids must point to failed observations"
                raise EvidenceValidationError(msg)


@dataclass(frozen=True, slots=True)
class RejectionEvidence:
    """One full durable signal rejection record, never a sampled near-miss."""

    evidence_id: str
    timestamp: datetime
    ts_init: datetime
    trading_date: date
    direction: str
    evaluation_sequence: int
    reached_layers: tuple[str, ...]
    condition_facts: tuple[ConditionFact, ...]
    blocking_condition_ids: tuple[str, ...]
    context: SignalEvaluationContext
    source_event_sequences: tuple[int, ...]

    def __post_init__(self) -> None:
        _require_machine_id(self.evidence_id, "evidence_id")
        if type(self.trading_date) is not date:
            msg = "rejection trading_date must be an exchange date label"
            raise EvidenceValidationError(msg)
        timestamp = _as_utc(self.timestamp, "rejection timestamp")
        ts_init = _as_utc(self.ts_init, "rejection ts_init")
        if ts_init < timestamp:
            msg = "rejection ts_init must not precede timestamp"
            raise EvidenceValidationError(msg)
        if self.evaluation_sequence <= 0:
            msg = "evaluation_sequence must be positive"
            raise EvidenceValidationError(msg)
        if not self.direction:
            msg = "rejection direction must not be blank"
            raise EvidenceValidationError(msg)
        if not self.reached_layers or not self.condition_facts:
            msg = "rejection evidence requires reached layers and condition facts"
            raise EvidenceValidationError(msg)
        if any(not _MACHINE_ID.fullmatch(layer) for layer in self.reached_layers):
            msg = "rejection reached_layers must contain stable machine identifiers"
            raise EvidenceValidationError(msg)
        if tuple(dict.fromkeys(self.reached_layers)) != self.reached_layers:
            msg = "rejection reached_layers must preserve each layer exactly once"
            raise EvidenceValidationError(msg)
        if len({fact.condition_id for fact in self.condition_facts}) != len(self.condition_facts):
            msg = "rejection evidence cannot repeat a condition fact"
            raise EvidenceValidationError(msg)
        facts_by_id = {fact.condition_id: fact for fact in self.condition_facts}
        if not self.blocking_condition_ids:
            msg = "rejection evidence requires blocking condition ids"
            raise EvidenceValidationError(msg)
        if tuple(sorted(set(self.blocking_condition_ids))) != self.blocking_condition_ids:
            msg = "blocking_condition_ids must be sorted and unique"
            raise EvidenceValidationError(msg)
        for condition_id in self.blocking_condition_ids:
            fact = facts_by_id.get(condition_id)
            if fact is None or fact.status != "failed":
                msg = "blocking condition must be represented by a failed condition fact"
                raise EvidenceValidationError(msg)
        sources = _normalize_sequences(self.source_event_sequences)
        if not sources:
            msg = "rejection evidence requires source event sequences"
            raise EvidenceValidationError(msg)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "ts_init", ts_init)
        object.__setattr__(self, "source_event_sequences", sources)

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "timestamp": _iso(self.timestamp),
            "ts_init": _iso(self.ts_init),
            "trading_date": self.trading_date.isoformat(),
            "direction": self.direction,
            "evaluation_sequence": self.evaluation_sequence,
            "reached_layers": list(self.reached_layers),
            "condition_facts": [fact.to_dict() for fact in self.condition_facts],
            "blocking_condition_ids": list(self.blocking_condition_ids),
            "context": self.context.to_dict(),
            "source_event_sequences": list(self.source_event_sequences),
        }


@dataclass(frozen=True, slots=True)
class EntryDecisionCapture:
    """Runtime entry/stop inputs carried from a created signal into execution."""

    signal_kind: str
    signal_timestamp: datetime
    entry_reference: float
    stop_reference_type: str
    stop_reference_price: float
    stop_offset_ticks: int
    final_stop_price: float
    condition_observations: tuple[ConditionObservation, ...]
    signal_event_ref: EventRef
    intent_event_ref: EventRef | None = None

    def __post_init__(self) -> None:
        if not self.signal_kind or not self.stop_reference_type:
            msg = "entry capture requires signal_kind and stop_reference_type"
            raise EvidenceValidationError(msg)
        for name, value in (
            ("entry_reference", self.entry_reference),
            ("stop_reference_price", self.stop_reference_price),
            ("final_stop_price", self.final_stop_price),
        ):
            _require_finite_number(value, name)
        if self.stop_offset_ticks < 0:
            msg = "stop_offset_ticks must not be negative"
            raise EvidenceValidationError(msg)
        if not self.condition_observations:
            msg = "entry capture requires condition observations"
            raise EvidenceValidationError(msg)
        signal_timestamp = _as_utc(self.signal_timestamp, "signal timestamp")
        object.__setattr__(self, "signal_timestamp", signal_timestamp)

    def with_intent_event(self, event_ref: EventRef) -> EntryDecisionCapture:
        """Bind the capture to the later breakout-intent event without recomputing facts."""
        return EntryDecisionCapture(
            signal_kind=self.signal_kind,
            signal_timestamp=self.signal_timestamp,
            entry_reference=self.entry_reference,
            stop_reference_type=self.stop_reference_type,
            stop_reference_price=self.stop_reference_price,
            stop_offset_ticks=self.stop_offset_ticks,
            final_stop_price=self.final_stop_price,
            condition_observations=self.condition_observations,
            signal_event_ref=self.signal_event_ref,
            intent_event_ref=event_ref,
        )


@dataclass(frozen=True, slots=True)
class ExitCandidateCapture:
    """One competing same-bar exit condition observed before conservative selection."""

    candidate_id: str
    triggered: bool
    reference_price: float
    observed_price: float

    def __post_init__(self) -> None:
        _require_machine_id(self.candidate_id, "exit candidate_id")
        _require_finite_number(self.reference_price, "exit candidate reference_price")
        _require_finite_number(self.observed_price, "exit candidate observed_price")


@dataclass(frozen=True, slots=True)
class ExitDecisionCapture:
    """Runtime exit choice plus every candidate considered on its deciding bar."""

    reason: str
    timestamp: datetime
    raw_exit_price: float
    selected_candidate_id: str
    candidates: tuple[ExitCandidateCapture, ...]
    resolution_code: str | None
    event_ref: EventRef

    def __post_init__(self) -> None:
        if not self.reason:
            msg = "exit capture requires a reason"
            raise EvidenceValidationError(msg)
        _require_machine_id(self.selected_candidate_id, "selected_candidate_id")
        if not self.candidates:
            msg = "exit capture requires same-bar candidates"
            raise EvidenceValidationError(msg)
        candidates = {candidate.candidate_id: candidate for candidate in self.candidates}
        selected = candidates.get(self.selected_candidate_id)
        if selected is None or not selected.triggered:
            msg = "selected exit candidate must exist and be triggered"
            raise EvidenceValidationError(msg)
        _require_finite_number(self.raw_exit_price, "raw_exit_price")
        object.__setattr__(self, "timestamp", _as_utc(self.timestamp, "exit timestamp"))


@dataclass(frozen=True, slots=True)
class ConservativeAssumptionCapture:
    """One machine-stable conservative assumption captured with a trade decision."""

    code: str
    applied: bool
    effects: tuple[str, ...]
    source_event_refs: tuple[EventRef, ...]

    def __post_init__(self) -> None:
        _require_machine_id(self.code, "assumption code")
        if not self.effects or any(not value for value in self.effects):
            msg = "conservative assumption requires explicit effects"
            raise EvidenceValidationError(msg)
        if not self.source_event_refs:
            msg = "conservative assumption requires source event refs"
            raise EvidenceValidationError(msg)


@dataclass(frozen=True, slots=True)
class TradeDecisionCapture:
    """Internal per-trade capture that remains typed until final event resequencing."""

    entry: EntryDecisionCapture
    entry_timestamp: datetime
    fill_price: float
    entry_fill_event_ref: EventRef
    exit: ExitDecisionCapture
    entry_slippage_ticks: int
    exit_slippage_ticks: int
    assumptions: tuple[ConservativeAssumptionCapture, ...]

    def __post_init__(self) -> None:
        entry_timestamp = _as_utc(self.entry_timestamp, "entry timestamp")
        object.__setattr__(self, "entry_timestamp", entry_timestamp)
        _require_finite_number(self.fill_price, "entry fill_price")
        if self.entry_slippage_ticks < 0 or self.exit_slippage_ticks < 0:
            msg = "slippage ticks must not be negative"
            raise EvidenceValidationError(msg)


@dataclass(frozen=True, slots=True)
class ConservativeAssumption:
    """Durable presentation-neutral conservative rule evidence."""

    code: str
    applied: bool
    effects: tuple[str, ...]
    source_sequences: tuple[int, ...]

    def __post_init__(self) -> None:
        _require_machine_id(self.code, "assumption code")
        if not self.effects:
            msg = "conservative assumption requires effects"
            raise EvidenceValidationError(msg)
        sources = _normalize_sequences(self.source_sequences)
        if not sources:
            msg = "conservative assumption requires source sequences"
            raise EvidenceValidationError(msg)
        object.__setattr__(self, "source_sequences", sources)

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "applied": self.applied,
            "effects": list(self.effects),
            "source_sequences": list(self.source_sequences),
        }


@dataclass(frozen=True, slots=True)
class EntryDecisionEvidence:
    """Final entry evidence for one completed trade."""

    signal_kind: str
    signal_timestamp: datetime
    entry_timestamp: datetime
    condition_facts: tuple[ConditionFact, ...]
    entry_reference: float
    fill_price: float
    source_event_sequences: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.signal_kind or not self.condition_facts:
            msg = "entry decision evidence requires a signal kind and condition facts"
            raise EvidenceValidationError(msg)
        if len({fact.condition_id for fact in self.condition_facts}) != len(
            self.condition_facts
        ):
            msg = "entry decision evidence cannot repeat a condition fact"
            raise EvidenceValidationError(msg)
        signal_timestamp = _as_utc(self.signal_timestamp, "signal timestamp")
        entry_timestamp = _as_utc(self.entry_timestamp, "entry timestamp")
        object.__setattr__(self, "signal_timestamp", signal_timestamp)
        object.__setattr__(self, "entry_timestamp", entry_timestamp)
        _require_finite_number(self.entry_reference, "entry_reference")
        _require_finite_number(self.fill_price, "fill_price")
        sources = _normalize_sequences(self.source_event_sequences)
        if not sources:
            msg = "entry decision evidence requires source sequences"
            raise EvidenceValidationError(msg)
        object.__setattr__(self, "source_event_sequences", sources)

    def to_dict(self) -> dict[str, object]:
        return {
            "signal_kind": self.signal_kind,
            "signal_timestamp": _iso(self.signal_timestamp),
            "entry_timestamp": _iso(self.entry_timestamp),
            "condition_facts": [fact.to_dict() for fact in self.condition_facts],
            "entry_reference": self.entry_reference,
            "fill_price": self.fill_price,
            "source_event_sequences": list(self.source_event_sequences),
        }


@dataclass(frozen=True, slots=True)
class StopDecisionEvidence:
    """Final stop-placement evidence for one completed trade."""

    reference_type: str
    reference_price: float
    offset_ticks: int
    final_stop_price: float
    condition_facts: tuple[ConditionFact, ...]
    source_event_sequences: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.reference_type or not self.condition_facts:
            msg = "stop decision evidence requires a reference type and condition facts"
            raise EvidenceValidationError(msg)
        if len({fact.condition_id for fact in self.condition_facts}) != len(
            self.condition_facts
        ):
            msg = "stop decision evidence cannot repeat a condition fact"
            raise EvidenceValidationError(msg)
        _require_finite_number(self.reference_price, "stop reference_price")
        _require_finite_number(self.final_stop_price, "final_stop_price")
        if self.offset_ticks < 0:
            msg = "stop offset_ticks must not be negative"
            raise EvidenceValidationError(msg)
        sources = _normalize_sequences(self.source_event_sequences)
        if not sources:
            msg = "stop decision evidence requires source sequences"
            raise EvidenceValidationError(msg)
        object.__setattr__(self, "source_event_sequences", sources)

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_type": self.reference_type,
            "reference_price": self.reference_price,
            "offset_ticks": self.offset_ticks,
            "final_stop_price": self.final_stop_price,
            "condition_facts": [fact.to_dict() for fact in self.condition_facts],
            "source_event_sequences": list(self.source_event_sequences),
        }


@dataclass(frozen=True, slots=True)
class ExitCandidate:
    """Final same-bar exit candidate retained even when it was not selected."""

    candidate_id: str
    triggered: bool
    reference_price: float
    observed_price: float

    def __post_init__(self) -> None:
        _require_machine_id(self.candidate_id, "exit candidate_id")
        _require_finite_number(self.reference_price, "exit candidate reference_price")
        _require_finite_number(self.observed_price, "exit candidate observed_price")

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "triggered": self.triggered,
            "reference_price": self.reference_price,
            "observed_price": self.observed_price,
        }


@dataclass(frozen=True, slots=True)
class ExitDecisionEvidence:
    """Final exit result plus all competing candidates in the deciding bar."""

    reason: str
    timestamp: datetime
    price: float
    candidates: tuple[ExitCandidate, ...]
    selected_candidate_id: str
    resolution_code: str | None
    source_event_sequences: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.reason or not self.candidates:
            msg = "exit decision evidence requires a reason and candidates"
            raise EvidenceValidationError(msg)
        if len({candidate.candidate_id for candidate in self.candidates}) != len(
            self.candidates
        ):
            msg = "exit decision evidence cannot repeat a candidate"
            raise EvidenceValidationError(msg)
        candidates = {candidate.candidate_id: candidate for candidate in self.candidates}
        selected = candidates.get(self.selected_candidate_id)
        if selected is None or not selected.triggered:
            msg = "selected exit candidate must exist and be triggered"
            raise EvidenceValidationError(msg)
        object.__setattr__(self, "timestamp", _as_utc(self.timestamp, "exit timestamp"))
        _require_finite_number(self.price, "exit price")
        sources = _normalize_sequences(self.source_event_sequences)
        if not sources:
            msg = "exit decision evidence requires source sequences"
            raise EvidenceValidationError(msg)
        object.__setattr__(self, "source_event_sequences", sources)

    def to_dict(self) -> dict[str, object]:
        return {
            "reason": self.reason,
            "timestamp": _iso(self.timestamp),
            "price": self.price,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "selected_candidate_id": self.selected_candidate_id,
            "resolution_code": self.resolution_code,
            "source_event_sequences": list(self.source_event_sequences),
        }


@dataclass(frozen=True, slots=True)
class TradeDecisionEvidence:
    """The four Owner-facing causal segments for one exact trade/ordinal."""

    trade_id: str
    ordinal: int
    entry: EntryDecisionEvidence
    stop: StopDecisionEvidence
    exit: ExitDecisionEvidence
    conservative_assumptions: tuple[ConservativeAssumption, ...]

    def __post_init__(self) -> None:
        if not _TRADE_ID.fullmatch(self.trade_id):
            msg = "decision evidence trade_id is not path-safe"
            raise EvidenceValidationError(msg)
        if self.ordinal <= 0:
            msg = "decision evidence ordinal must be positive"
            raise EvidenceValidationError(msg)
        if not self.conservative_assumptions:
            msg = "decision evidence requires conservative assumptions"
            raise EvidenceValidationError(msg)
        if len({item.code for item in self.conservative_assumptions}) != len(
            self.conservative_assumptions
        ):
            msg = "decision evidence cannot repeat a conservative assumption"
            raise EvidenceValidationError(msg)

    def to_dict(self) -> dict[str, object]:
        return {
            "trade_id": self.trade_id,
            "ordinal": self.ordinal,
            "entry": self.entry.to_dict(),
            "stop": self.stop.to_dict(),
            "exit": self.exit.to_dict(),
            "conservative_assumptions": [
                assumption.to_dict() for assumption in self.conservative_assumptions
            ],
        }


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    """Deterministic projection of full evidence records, never an independent counter."""

    availability: Literal["available", "unavailable"]
    complete: bool
    evaluation_count: int
    rejection_count: int
    layer_reached_counts: Mapping[str, int]
    blocking_condition_counts: Mapping[str, int]
    deepest_layer: str | None
    trade_count: int

    def __post_init__(self) -> None:
        if self.availability == "available" and not self.complete:
            msg = "available evidence must be complete"
            raise EvidenceValidationError(msg)
        if self.availability == "unavailable" and self.complete:
            msg = "unavailable evidence cannot claim completeness"
            raise EvidenceValidationError(msg)
        if self.evaluation_count < 0 or self.rejection_count < 0 or self.trade_count < 0:
            msg = "evidence summary counts must not be negative"
            raise EvidenceValidationError(msg)
        if self.rejection_count > self.evaluation_count:
            msg = "rejection_count cannot exceed evaluation_count"
            raise EvidenceValidationError(msg)
        for mapping_name, mapping in (
            ("layer_reached_counts", self.layer_reached_counts),
            ("blocking_condition_counts", self.blocking_condition_counts),
        ):
            for key, count in mapping.items():
                _require_machine_id(key, mapping_name)
                if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                    msg = f"{mapping_name} values must be non-negative integers"
                    raise EvidenceValidationError(msg)

    def to_dict(self) -> dict[str, object]:
        return {
            "availability": self.availability,
            "complete": self.complete,
            "evaluation_count": self.evaluation_count,
            "rejection_count": self.rejection_count,
            "layer_reached_counts": dict(sorted(self.layer_reached_counts.items())),
            "blocking_condition_counts": dict(sorted(self.blocking_condition_counts.items())),
            "deepest_layer": self.deepest_layer,
            "trade_count": self.trade_count,
        }


def build_rejection_evidence(
    events: Sequence[object],
    *,
    trading_date_for_timestamp: Callable[[datetime], date],
) -> tuple[RejectionEvidence, ...]:
    """Materialize captures from final globally resequenced events without re-evaluation."""
    event_sequences = _event_sequences_by_ref(events)
    records: list[RejectionEvidence] = []
    for event in events:
        capture = getattr(event, "rejection_capture", None)
        if capture is None:
            continue
        if not isinstance(capture, RejectionCapture):
            msg = "strategy event rejection capture has an invalid type"
            raise EvidenceValidationError(msg)
        event_ref = _event_ref_for(event)
        final_sequence = _resolve_event_refs((event_ref,), event_sequences)
        facts = tuple(
            ConditionFact.from_observation(
                observation,
                source_sequences=final_sequence,
            )
            for observation in capture.condition_observations
        )
        timestamp = getattr(event, "timestamp", None)
        ts_init = getattr(event, "ts_init", None)
        direction = getattr(event, "direction", None)
        if not isinstance(timestamp, datetime) or not isinstance(ts_init, datetime):
            msg = "rejection evidence event must carry canonical timestamps"
            raise EvidenceValidationError(msg)
        direction_value = getattr(direction, "value", None)
        direction_text = direction_value if isinstance(direction_value, str) else str(direction)
        records.append(
            RejectionEvidence(
                evidence_id=capture.evidence_id,
                timestamp=timestamp,
                ts_init=ts_init,
                trading_date=trading_date_for_timestamp(timestamp),
                direction=direction_text,
                evaluation_sequence=capture.evaluation_sequence,
                reached_layers=capture.reached_layers,
                condition_facts=facts,
                blocking_condition_ids=capture.blocking_condition_ids,
                context=capture.context,
                source_event_sequences=final_sequence,
            )
        )
    ordered = tuple(
        sorted(records, key=lambda item: (item.evaluation_sequence, item.evidence_id))
    )
    if len({record.evidence_id for record in ordered}) != len(ordered):
        msg = "rejection evidence ids must be unique within a run"
        raise EvidenceValidationError(msg)
    return ordered


def build_trade_decision_evidence(
    capture: TradeDecisionCapture,
    *,
    trade_id: str,
    ordinal: int,
    events: Sequence[object],
) -> TradeDecisionEvidence:
    """Resolve an engine-captured trade decision into a final durable four-part record."""
    event_sequences = _event_sequences_by_ref(events)
    entry_refs: tuple[EventRef, ...] = (capture.entry.signal_event_ref,)
    if capture.entry.intent_event_ref is not None:
        entry_refs = (*entry_refs, capture.entry.intent_event_ref)
    entry_refs = (*entry_refs, capture.entry_fill_event_ref)
    entry_sequences = _resolve_event_refs(entry_refs, event_sequences)
    entry_facts = tuple(
        ConditionFact.from_observation(observation, source_sequences=entry_sequences)
        for observation in capture.entry.condition_observations
    )
    stop_observations = (
        ConditionObservation(
            condition_id="stop_reference_price",
            layer_id="execution",
            observed_at=capture.entry_timestamp,
            status="passed",
            actual=capture.entry.stop_reference_price,
            operator="eq",
            required=capture.entry.stop_reference_price,
            unit="price",
        ),
        ConditionObservation(
            condition_id="stop_offset_ticks",
            layer_id="execution",
            observed_at=capture.entry_timestamp,
            status="passed",
            actual=capture.entry.stop_offset_ticks,
            operator="eq",
            required=capture.entry.stop_offset_ticks,
            unit="ticks",
        ),
        ConditionObservation(
            condition_id="stop_final_price",
            layer_id="execution",
            observed_at=capture.entry_timestamp,
            status="passed",
            actual=capture.entry.final_stop_price,
            operator="eq",
            required=capture.entry.final_stop_price,
            unit="price",
        ),
    )
    stop_facts = tuple(
        ConditionFact.from_observation(observation, source_sequences=entry_sequences)
        for observation in stop_observations
    )
    exit_sequences = _resolve_event_refs((capture.exit.event_ref,), event_sequences)
    assumptions = tuple(
        ConservativeAssumption(
            code=item.code,
            applied=item.applied,
            effects=item.effects,
            source_sequences=_resolve_event_refs(item.source_event_refs, event_sequences),
        )
        for item in capture.assumptions
    )
    return TradeDecisionEvidence(
        trade_id=trade_id,
        ordinal=ordinal,
        entry=EntryDecisionEvidence(
            signal_kind=capture.entry.signal_kind,
            signal_timestamp=capture.entry.signal_timestamp,
            entry_timestamp=capture.entry_timestamp,
            condition_facts=entry_facts,
            entry_reference=capture.entry.entry_reference,
            fill_price=capture.fill_price,
            source_event_sequences=entry_sequences,
        ),
        stop=StopDecisionEvidence(
            reference_type=capture.entry.stop_reference_type,
            reference_price=capture.entry.stop_reference_price,
            offset_ticks=capture.entry.stop_offset_ticks,
            final_stop_price=capture.entry.final_stop_price,
            condition_facts=stop_facts,
            source_event_sequences=entry_sequences,
        ),
        exit=ExitDecisionEvidence(
            reason=capture.exit.reason,
            timestamp=capture.exit.timestamp,
            price=capture.exit.raw_exit_price,
            candidates=tuple(
                ExitCandidate(
                    candidate_id=item.candidate_id,
                    triggered=item.triggered,
                    reference_price=item.reference_price,
                    observed_price=item.observed_price,
                )
                for item in capture.exit.candidates
            ),
            selected_candidate_id=capture.exit.selected_candidate_id,
            resolution_code=capture.exit.resolution_code,
            source_event_sequences=exit_sequences,
        ),
        conservative_assumptions=assumptions,
    )


def build_evidence_summary(
    *,
    events: Sequence[object],
    rejection_evidence: Sequence[RejectionEvidence],
    trade_count: int,
    complete: bool,
) -> EvidenceSummary:
    """Derive every summary count from immutable event/evidence records."""
    if not complete:
        return EvidenceSummary(
            availability="unavailable",
            complete=False,
            evaluation_count=0,
            rejection_count=0,
            layer_reached_counts={},
            blocking_condition_counts={},
            deepest_layer=None,
            trade_count=trade_count,
        )
    rejected_event_count = sum(
        1
        for event in events
        if getattr(getattr(event, "event_type", None), "value", None)
        == "signal_rejected"
    )
    created_event_count = sum(
        1
        for event in events
        if getattr(getattr(event, "event_type", None), "value", None)
        == "signal_created"
    )
    if rejected_event_count != len(rejection_evidence):
        msg = "complete rejection evidence count must match signal_rejected event count"
        raise EvidenceValidationError(msg)
    layer_counts: Counter[str] = Counter()
    blocking_counts: Counter[str] = Counter()
    deepest: str | None = None
    for record in rejection_evidence:
        layer_counts.update(record.reached_layers)
        blocking_counts.update(record.blocking_condition_ids)
        for layer in record.reached_layers:
            if deepest is None or _layer_key(layer) > _layer_key(deepest):
                deepest = layer
    return EvidenceSummary(
        availability="available",
        complete=True,
        evaluation_count=created_event_count + rejected_event_count,
        rejection_count=len(rejection_evidence),
        layer_reached_counts=dict(layer_counts),
        blocking_condition_counts=dict(blocking_counts),
        deepest_layer=deepest,
        trade_count=trade_count,
    )


def validate_evidence_bundle(
    *,
    events: Sequence[object],
    rejection_evidence: Sequence[RejectionEvidence],
    evidence_summary: EvidenceSummary,
    trade_evidence: Iterable[TradeDecisionEvidence],
    expected_trade_count: int,
    complete: bool,
) -> None:
    """Reject malformed/dangling/partial new evidence before any artifact publication."""
    if not complete:
        if rejection_evidence or evidence_summary.complete:
            msg = "incomplete evidence cannot expose durable evidence records"
            raise EvidenceValidationError(msg)
        return
    sequence_set = {
        value
        for event in events
        for value in (getattr(event, "sequence", None),)
        if isinstance(value, int) and value > 0
    }
    if len(sequence_set) != len(events):
        msg = "event sequence references must be unique before evidence validation"
        raise EvidenceValidationError(msg)
    trade_evidence_values = tuple(trade_evidence)
    if len(trade_evidence_values) != expected_trade_count:
        msg = "every completed trade requires exactly one decision evidence record"
        raise EvidenceValidationError(msg)
    if len({item.trade_id for item in trade_evidence_values}) != len(trade_evidence_values):
        msg = "decision evidence trade ids must be unique"
        raise EvidenceValidationError(msg)
    if [item.ordinal for item in trade_evidence_values] != list(
        range(1, expected_trade_count + 1)
    ):
        msg = "decision evidence ordinals must match trade order"
        raise EvidenceValidationError(msg)
    ordered_rejections = tuple(
        sorted(rejection_evidence, key=lambda item: (item.evaluation_sequence, item.evidence_id))
    )
    if tuple(rejection_evidence) != ordered_rejections:
        msg = "rejection evidence must be ordered by evaluation_sequence then evidence_id"
        raise EvidenceValidationError(msg)
    if len({item.evidence_id for item in rejection_evidence}) != len(rejection_evidence):
        msg = "rejection evidence ids must be unique"
        raise EvidenceValidationError(msg)
    rejected_event_sequences = {
        getattr(event, "sequence", None)
        for event in events
        if getattr(getattr(event, "event_type", None), "value", None) == "signal_rejected"
    }
    if not all(isinstance(value, int) and value > 0 for value in rejected_event_sequences):
        msg = "signal_rejected events must have valid final sequences"
        raise EvidenceValidationError(msg)
    if any(len(record.source_event_sequences) != 1 for record in rejection_evidence):
        msg = "each rejection record must map to exactly one signal_rejected event"
        raise EvidenceValidationError(msg)
    rejection_sources = [record.source_event_sequences[0] for record in rejection_evidence]
    if (
        len(set(rejection_sources)) != len(rejection_sources)
        or rejected_event_sequences != set(rejection_sources)
    ):
        msg = "every signal_rejected event requires exactly one evidence source record"
        raise EvidenceValidationError(msg)
    for record in rejection_evidence:
        _assert_sequences_exist(record.source_event_sequences, sequence_set)
        for fact in record.condition_facts:
            _assert_sequences_exist(fact.source_sequences, sequence_set)
    for trade_record in trade_evidence_values:
        _assert_sequences_exist(trade_record.entry.source_event_sequences, sequence_set)
        _assert_sequences_exist(trade_record.stop.source_event_sequences, sequence_set)
        for fact in (*trade_record.entry.condition_facts, *trade_record.stop.condition_facts):
            _assert_sequences_exist(fact.source_sequences, sequence_set)
        _assert_sequences_exist(trade_record.exit.source_event_sequences, sequence_set)
        for assumption in trade_record.conservative_assumptions:
            _assert_sequences_exist(assumption.source_sequences, sequence_set)
    rebuilt = build_evidence_summary(
        events=events,
        rejection_evidence=rejection_evidence,
        trade_count=expected_trade_count,
        complete=True,
    )
    if rebuilt.to_dict() != evidence_summary.to_dict():
        msg = "evidence_summary must be derived exactly from full evidence records"
        raise EvidenceValidationError(msg)


@dataclass(frozen=True, slots=True)
class _SerializedEventType:
    """Minimal typed event vocabulary adapter used only by artifact validation."""

    value: str


@dataclass(frozen=True, slots=True)
class _SerializedEvent:
    """Read-only event projection needed by the canonical evidence validator."""

    sequence: int
    event_type: _SerializedEventType


def validate_serialized_evidence_bundle(
    *,
    events: object,
    rejection_evidence: object,
    evidence_summary: object,
    trades: object,
) -> None:
    """Strictly decode persisted JSON then reuse the canonical typed invariants.

    New-complete sidecars are untrusted input at read time.  Parsing every
    nested record back into the same dataclasses used by the engine prevents a
    second, weaker set of key-only checks from becoming the reader contract.
    """
    parsed_events = tuple(
        _parse_serialized_event(item, path=f"events[{index}]")
        for index, item in enumerate(_require_json_list(events, "events"))
    )
    parsed_rejections = tuple(
        _parse_rejection_evidence(item, path=f"rejection_evidence[{index}]")
        for index, item in enumerate(
            _require_json_list(rejection_evidence, "rejection_evidence")
        )
    )
    parsed_trades = tuple(
        _parse_trade_record(item, path=f"trades[{index}]")
        for index, item in enumerate(_require_json_list(trades, "trades"))
    )
    parsed_summary = _parse_evidence_summary(evidence_summary)
    validate_evidence_bundle(
        events=parsed_events,
        rejection_evidence=parsed_rejections,
        evidence_summary=parsed_summary,
        trade_evidence=parsed_trades,
        expected_trade_count=len(parsed_trades),
        complete=True,
    )


def _parse_serialized_event(value: object, *, path: str) -> _SerializedEvent:
    record = _require_json_mapping(value, path)
    return _SerializedEvent(
        sequence=_require_positive_int(_required(record, "sequence", path), f"{path}.sequence"),
        event_type=_SerializedEventType(
            _require_text(_required(record, "event_type", path), f"{path}.event_type")
        ),
    )


def _parse_trade_record(value: object, *, path: str) -> TradeDecisionEvidence:
    record = _require_json_mapping(value, path)
    outer_trade_id = _require_text(_required(record, "trade_id", path), f"{path}.trade_id")
    evidence = _parse_trade_decision_evidence(
        _required(record, "decision_evidence", path),
        path=f"{path}.decision_evidence",
    )
    if evidence.trade_id != outer_trade_id:
        msg = f"{path}.decision_evidence.trade_id must match the outer trade_id"
        raise EvidenceValidationError(msg)
    return evidence


def _parse_trade_decision_evidence(
    value: object,
    *,
    path: str,
) -> TradeDecisionEvidence:
    record = _require_json_mapping(value, path)
    entry = _parse_entry_evidence(_required(record, "entry", path), path=f"{path}.entry")
    stop = _parse_stop_evidence(_required(record, "stop", path), path=f"{path}.stop")
    exit_evidence = _parse_exit_evidence(
        _required(record, "exit", path), path=f"{path}.exit"
    )
    assumptions = tuple(
        _parse_conservative_assumption(item, path=f"{path}.conservative_assumptions[{index}]")
        for index, item in enumerate(
            _require_json_list(
                _required(record, "conservative_assumptions", path),
                f"{path}.conservative_assumptions",
            )
        )
    )
    return TradeDecisionEvidence(
        trade_id=_require_text(_required(record, "trade_id", path), f"{path}.trade_id"),
        ordinal=_require_positive_int(_required(record, "ordinal", path), f"{path}.ordinal"),
        entry=entry,
        stop=stop,
        exit=exit_evidence,
        conservative_assumptions=assumptions,
    )


def _parse_entry_evidence(value: object, *, path: str) -> EntryDecisionEvidence:
    record = _require_json_mapping(value, path)
    return EntryDecisionEvidence(
        signal_kind=_require_text(
            _required(record, "signal_kind", path), f"{path}.signal_kind"
        ),
        signal_timestamp=_require_datetime(
            _required(record, "signal_timestamp", path), f"{path}.signal_timestamp"
        ),
        entry_timestamp=_require_datetime(
            _required(record, "entry_timestamp", path), f"{path}.entry_timestamp"
        ),
        condition_facts=_parse_condition_facts(
            _required(record, "condition_facts", path), f"{path}.condition_facts"
        ),
        entry_reference=_require_finite_float(
            _required(record, "entry_reference", path), f"{path}.entry_reference"
        ),
        fill_price=_require_finite_float(
            _required(record, "fill_price", path), f"{path}.fill_price"
        ),
        source_event_sequences=_require_sequences(
            _required(record, "source_event_sequences", path),
            f"{path}.source_event_sequences",
        ),
    )


def _parse_stop_evidence(value: object, *, path: str) -> StopDecisionEvidence:
    record = _require_json_mapping(value, path)
    return StopDecisionEvidence(
        reference_type=_require_text(
            _required(record, "reference_type", path), f"{path}.reference_type"
        ),
        reference_price=_require_finite_float(
            _required(record, "reference_price", path), f"{path}.reference_price"
        ),
        offset_ticks=_require_non_negative_int(
            _required(record, "offset_ticks", path), f"{path}.offset_ticks"
        ),
        final_stop_price=_require_finite_float(
            _required(record, "final_stop_price", path), f"{path}.final_stop_price"
        ),
        condition_facts=_parse_condition_facts(
            _required(record, "condition_facts", path), f"{path}.condition_facts"
        ),
        source_event_sequences=_require_sequences(
            _required(record, "source_event_sequences", path),
            f"{path}.source_event_sequences",
        ),
    )


def _parse_exit_evidence(value: object, *, path: str) -> ExitDecisionEvidence:
    record = _require_json_mapping(value, path)
    candidates = tuple(
        _parse_exit_candidate(item, path=f"{path}.candidates[{index}]")
        for index, item in enumerate(
            _require_json_list(_required(record, "candidates", path), f"{path}.candidates")
        )
    )
    return ExitDecisionEvidence(
        reason=_require_text(_required(record, "reason", path), f"{path}.reason"),
        timestamp=_require_datetime(
            _required(record, "timestamp", path), f"{path}.timestamp"
        ),
        price=_require_finite_float(_required(record, "price", path), f"{path}.price"),
        candidates=candidates,
        selected_candidate_id=_require_text(
            _required(record, "selected_candidate_id", path),
            f"{path}.selected_candidate_id",
        ),
        resolution_code=_require_optional_text(
            _required(record, "resolution_code", path), f"{path}.resolution_code"
        ),
        source_event_sequences=_require_sequences(
            _required(record, "source_event_sequences", path),
            f"{path}.source_event_sequences",
        ),
    )


def _parse_exit_candidate(value: object, *, path: str) -> ExitCandidate:
    record = _require_json_mapping(value, path)
    return ExitCandidate(
        candidate_id=_require_text(
            _required(record, "candidate_id", path), f"{path}.candidate_id"
        ),
        triggered=_require_bool(
            _required(record, "triggered", path), f"{path}.triggered"
        ),
        reference_price=_require_finite_float(
            _required(record, "reference_price", path), f"{path}.reference_price"
        ),
        observed_price=_require_finite_float(
            _required(record, "observed_price", path), f"{path}.observed_price"
        ),
    )


def _parse_conservative_assumption(
    value: object,
    *,
    path: str,
) -> ConservativeAssumption:
    record = _require_json_mapping(value, path)
    return ConservativeAssumption(
        code=_require_text(_required(record, "code", path), f"{path}.code"),
        applied=_require_bool(_required(record, "applied", path), f"{path}.applied"),
        effects=tuple(
            _require_text(item, f"{path}.effects[{index}]")
            for index, item in enumerate(
                _require_json_list(_required(record, "effects", path), f"{path}.effects")
            )
        ),
        source_sequences=_require_sequences(
            _required(record, "source_sequences", path), f"{path}.source_sequences"
        ),
    )


def _parse_rejection_evidence(value: object, *, path: str) -> RejectionEvidence:
    record = _require_json_mapping(value, path)
    context = _parse_signal_context(_required(record, "context", path), path=f"{path}.context")
    return RejectionEvidence(
        evidence_id=_require_text(
            _required(record, "evidence_id", path), f"{path}.evidence_id"
        ),
        timestamp=_require_datetime(
            _required(record, "timestamp", path), f"{path}.timestamp"
        ),
        ts_init=_require_datetime(_required(record, "ts_init", path), f"{path}.ts_init"),
        trading_date=_require_date(
            _required(record, "trading_date", path), f"{path}.trading_date"
        ),
        direction=_require_text(
            _required(record, "direction", path), f"{path}.direction"
        ),
        evaluation_sequence=_require_positive_int(
            _required(record, "evaluation_sequence", path),
            f"{path}.evaluation_sequence",
        ),
        reached_layers=tuple(
            _require_text(item, f"{path}.reached_layers[{index}]")
            for index, item in enumerate(
                _require_json_list(
                    _required(record, "reached_layers", path), f"{path}.reached_layers"
                )
            )
        ),
        condition_facts=_parse_condition_facts(
            _required(record, "condition_facts", path), f"{path}.condition_facts"
        ),
        blocking_condition_ids=tuple(
            _require_text(item, f"{path}.blocking_condition_ids[{index}]")
            for index, item in enumerate(
                _require_json_list(
                    _required(record, "blocking_condition_ids", path),
                    f"{path}.blocking_condition_ids",
                )
            )
        ),
        context=context,
        source_event_sequences=_require_sequences(
            _required(record, "source_event_sequences", path),
            f"{path}.source_event_sequences",
        ),
    )


def _parse_signal_context(value: object, *, path: str) -> SignalEvaluationContext:
    record = _require_json_mapping(value, path)
    inside_count_raw = _required(record, "inside_count", path)
    daily_regime_raw = _required(record, "daily_regime", path)
    return SignalEvaluationContext(
        candidate_signal_kinds=tuple(
            _require_text(item, f"{path}.candidate_signal_kinds[{index}]")
            for index, item in enumerate(
                _require_json_list(
                    _required(record, "candidate_signal_kinds", path),
                    f"{path}.candidate_signal_kinds",
                )
            )
        ),
        inside_count=(
            None
            if inside_count_raw is None
            else _require_positive_int(inside_count_raw, f"{path}.inside_count")
        ),
        entry_pullback_state=_require_text(
            _required(record, "entry_pullback_state", path),
            f"{path}.entry_pullback_state",
        ),
        mid_pullback_state=_require_text(
            _required(record, "mid_pullback_state", path),
            f"{path}.mid_pullback_state",
        ),
        daily_regime=(
            None
            if daily_regime_raw is None
            else _require_text(daily_regime_raw, f"{path}.daily_regime")
        ),
    )


def _parse_condition_facts(value: object, path: str) -> tuple[ConditionFact, ...]:
    return tuple(
        _parse_condition_fact(item, path=f"{path}[{index}]")
        for index, item in enumerate(_require_json_list(value, path))
    )


def _parse_condition_fact(value: object, *, path: str) -> ConditionFact:
    record = _require_json_mapping(value, path)
    status_text = _require_text(_required(record, "status", path), f"{path}.status")
    if status_text not in {"passed", "failed", "not_evaluated"}:
        msg = f"{path}.status is not a canonical condition status"
        raise EvidenceValidationError(msg)
    return ConditionFact(
        condition_id=_require_text(
            _required(record, "condition_id", path), f"{path}.condition_id"
        ),
        layer_id=_require_text(
            _required(record, "layer_id", path), f"{path}.layer_id"
        ),
        observed_at=_require_datetime(
            _required(record, "observed_at", path), f"{path}.observed_at"
        ),
        status=cast(ConditionStatus, status_text),
        actual=_parse_primitive_value(
            _required(record, "actual", path), f"{path}.actual"
        ),
        operator=_require_text(
            _required(record, "operator", path), f"{path}.operator"
        ),
        required=_parse_primitive_value(
            _required(record, "required", path), f"{path}.required"
        ),
        unit=_require_text(_required(record, "unit", path), f"{path}.unit"),
        source_sequences=_require_sequences(
            _required(record, "source_sequences", path), f"{path}.source_sequences"
        ),
    )


def _parse_evidence_summary(value: object) -> EvidenceSummary:
    path = "evidence_summary"
    record = _require_json_mapping(value, path)
    availability = _require_text(
        _required(record, "availability", path), f"{path}.availability"
    )
    complete = _require_bool(_required(record, "complete", path), f"{path}.complete")
    if availability != "available" or complete is not True:
        msg = "new-complete evidence_summary must be available and complete"
        raise EvidenceValidationError(msg)
    deepest_raw = _required(record, "deepest_layer", path)
    return EvidenceSummary(
        availability="available",
        complete=True,
        evaluation_count=_require_non_negative_int(
            _required(record, "evaluation_count", path), f"{path}.evaluation_count"
        ),
        rejection_count=_require_non_negative_int(
            _required(record, "rejection_count", path), f"{path}.rejection_count"
        ),
        layer_reached_counts=_require_count_mapping(
            _required(record, "layer_reached_counts", path),
            f"{path}.layer_reached_counts",
        ),
        blocking_condition_counts=_require_count_mapping(
            _required(record, "blocking_condition_counts", path),
            f"{path}.blocking_condition_counts",
        ),
        deepest_layer=(
            None
            if deepest_raw is None
            else _require_text(deepest_raw, f"{path}.deepest_layer")
        ),
        trade_count=_require_non_negative_int(
            _required(record, "trade_count", path), f"{path}.trade_count"
        ),
    )


def _required(record: Mapping[str, object], key: str, path: str) -> object:
    if key not in record:
        msg = f"{path} is missing required field {key}"
        raise EvidenceValidationError(msg)
    return record[key]


def _require_json_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        msg = f"{path} must be a JSON object"
        raise EvidenceValidationError(msg)
    return cast(Mapping[str, object], value)


def _require_json_list(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        msg = f"{path} must be a JSON array"
        raise EvidenceValidationError(msg)
    return cast(list[object], value)


def _require_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        msg = f"{path} must be non-blank text without surrounding whitespace"
        raise EvidenceValidationError(msg)
    return value


def _require_optional_text(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, path)


def _require_bool(value: object, path: str) -> bool:
    if type(value) is not bool:
        msg = f"{path} must be a boolean"
        raise EvidenceValidationError(msg)
    return value


def _require_positive_int(value: object, path: str) -> int:
    if type(value) is not int or value <= 0:
        msg = f"{path} must be a positive integer"
        raise EvidenceValidationError(msg)
    return value


def _require_non_negative_int(value: object, path: str) -> int:
    if type(value) is not int or value < 0:
        msg = f"{path} must be a non-negative integer"
        raise EvidenceValidationError(msg)
    return value


def _require_finite_float(value: object, path: str) -> float:
    _require_finite_number(value, path)
    return cast(float, value)


def _parse_primitive_value(value: object, path: str) -> PrimitiveValue:
    _require_primitive(value, path)
    return cast(PrimitiveValue, value)


def _require_datetime(value: object, path: str) -> datetime:
    text = _require_text(value, path)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        msg = f"{path} must be an ISO-8601 timestamp"
        raise EvidenceValidationError(msg) from exc
    return _as_utc(parsed, path)


def _require_date(value: object, path: str) -> date:
    text = _require_text(value, path)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        msg = f"{path} must be an ISO calendar date"
        raise EvidenceValidationError(msg) from exc
    if parsed.isoformat() != text:
        msg = f"{path} must use canonical YYYY-MM-DD form"
        raise EvidenceValidationError(msg)
    return parsed


def _require_sequences(value: object, path: str) -> tuple[int, ...]:
    return _normalize_sequences(
        tuple(
            _require_positive_int(item, f"{path}[{index}]")
            for index, item in enumerate(_require_json_list(value, path))
        )
    )


def _require_count_mapping(value: object, path: str) -> dict[str, int]:
    mapping = _require_json_mapping(value, path)
    return {
        _require_text(key, f"{path} key"): _require_non_negative_int(
            count, f"{path}.{key}"
        )
        for key, count in mapping.items()
    }


def _validate_condition_shape(
    *,
    condition_id: str,
    layer_id: str,
    observed_at: datetime,
    status: ConditionStatus,
    actual: PrimitiveValue,
    operator: str,
    required: PrimitiveValue,
    unit: str,
) -> None:
    _require_machine_id(condition_id, "condition_id")
    _require_machine_id(layer_id, "layer_id")
    _as_utc(observed_at, "condition observed_at")
    if status not in {"passed", "failed", "not_evaluated"}:
        msg = "condition status must be passed, failed, or not_evaluated"
        raise EvidenceValidationError(msg)
    if operator not in _ALLOWED_OPERATORS:
        msg = "condition operator is not an approved machine operator"
        raise EvidenceValidationError(msg)
    if not isinstance(unit, str) or not unit.strip() or unit != unit.strip():
        msg = "condition unit must be explicit non-blank text without surrounding whitespace"
        raise EvidenceValidationError(msg)
    _require_primitive(actual, "condition actual")
    _require_primitive(required, "condition required")
    if status == "not_evaluated" and (actual is not None or required is not None):
        msg = "not_evaluated condition facts must preserve unknown values as null"
        raise EvidenceValidationError(msg)


def _require_machine_id(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _MACHINE_ID.fullmatch(value) is None:
        msg = f"{field_name} must be a stable lowercase machine identifier"
        raise EvidenceValidationError(msg)


def _require_primitive(value: object, field_name: str) -> None:
    if value is None:
        return
    if type(value) not in {str, int, float, bool}:
        msg = f"{field_name} must be a native primitive or null"
        raise EvidenceValidationError(msg)
    if isinstance(value, float) and not isfinite(value):
        msg = f"{field_name} must be finite"
        raise EvidenceValidationError(msg)


def _require_finite_number(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        msg = f"{field_name} must be a finite number"
        raise EvidenceValidationError(msg)


def _normalize_sequences(values: Sequence[int]) -> tuple[int, ...]:
    normalized = tuple(values)
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in normalized
    ):
        msg = "source sequences must be positive integers"
        raise EvidenceValidationError(msg)
    if tuple(sorted(set(normalized))) != normalized:
        msg = "source sequences must be sorted and unique"
        raise EvidenceValidationError(msg)
    return normalized


def _event_ref_for(event: object) -> EventRef:
    origin = getattr(event, "origin", None)
    origin_sequence = getattr(event, "origin_sequence", None)
    if not isinstance(origin, EventOrigin) or not isinstance(origin_sequence, int):
        msg = "event lacks a typed origin reference for evidence"
        raise EvidenceValidationError(msg)
    return EventRef(origin=origin, origin_sequence=origin_sequence)


def _event_sequences_by_ref(events: Sequence[object]) -> dict[EventRef, int]:
    mapping: dict[EventRef, int] = {}
    for event in events:
        ref = _event_ref_for(event)
        sequence = getattr(event, "sequence", None)
        if not isinstance(sequence, int) or sequence <= 0:
            msg = "evidence event sequence must be positive"
            raise EvidenceValidationError(msg)
        if ref in mapping:
            msg = "event origin references must be unique"
            raise EvidenceValidationError(msg)
        mapping[ref] = sequence
    return mapping


def _resolve_event_refs(
    refs: Sequence[EventRef],
    mapping: Mapping[EventRef, int],
) -> tuple[int, ...]:
    values: list[int] = []
    for ref in refs:
        try:
            values.append(mapping[ref])
        except KeyError as exc:
            msg = (
                "evidence references an event absent from final log: "
                f"{ref.origin}:{ref.origin_sequence}"
            )
            raise EvidenceValidationError(msg) from exc
    return _normalize_sequences(tuple(sorted(set(values))))


def _assert_sequences_exist(values: Sequence[int], known: set[int]) -> None:
    if any(value not in known for value in values):
        msg = "evidence contains a dangling source event sequence"
        raise EvidenceValidationError(msg)


def _layer_key(layer: str) -> tuple[int, str]:
    return (_LAYER_ORDER.get(layer, len(_LAYER_ORDER)), layer)


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"{field_name} must include a timezone"
        raise EvidenceValidationError(msg)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
