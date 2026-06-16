"""Domain entities, value objects, and lifecycle enums (TRD §4, §6).

These are plain, mostly-immutable data structures with no I/O. Mutable lifecycle entities
(``Order``) expose explicit, guarded transitions; everything else is frozen.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from .currency import CurrencyPair
from .money import Money
from .rate import Bps, Rate


def new_id() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------------------------
# Enums (state machines documented in TRD §6 / ARCHITECTURE §5)
# --------------------------------------------------------------------------------------------
class Direction(StrEnum):
    HAVE = "HAVE"  # agent holds `given` and wants it in `target`
    OWE = "OWE"  # agent owes `given`; `target` is its book/settlement currency


class Timing(StrEnum):
    SPOT = "SPOT"
    FORWARD = "FORWARD"


class Instrument(StrEnum):
    CONVERT = "CONVERT"
    HEDGE = "HEDGE"


class ExposureStatus(StrEnum):
    DECLARED = "DECLARED"
    DECIDED = "DECIDED"
    QUOTED = "QUOTED"
    NEUTRALIZED = "NEUTRALIZED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class QuoteStatus(StrEnum):
    QUOTED = "QUOTED"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"  # parked above a mandate's approval threshold
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


#: Legal order state transitions (TRD §6). A retry of a FAILED order re-enters SUBMITTED;
#: an order parked for approval is released to SUBMITTED once a human signs off.
_ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset(
        {OrderStatus.SUBMITTED, OrderStatus.REJECTED, OrderStatus.APPROVAL_REQUIRED}
    ),
    OrderStatus.APPROVAL_REQUIRED: frozenset({OrderStatus.SUBMITTED, OrderStatus.REJECTED}),
    OrderStatus.SUBMITTED: frozenset({OrderStatus.FILLED, OrderStatus.FAILED}),
    OrderStatus.FAILED: frozenset({OrderStatus.SUBMITTED}),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
}


# --------------------------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Exposure:
    """A declared currency exposure to neutralize (aggregate root)."""

    agent_id: str
    principal_id: str
    given: Money
    target_currency: str
    direction: Direction
    timing: Timing
    created_at: datetime
    value_date: date | None = None
    status: ExposureStatus = ExposureStatus.DECLARED
    id: str = field(default_factory=new_id)

    def with_status(self, status: ExposureStatus) -> Exposure:
        return replace(self, status=status)


@dataclass(frozen=True, slots=True)
class Decision:
    """The normalization output: convert (spot) vs hedge (forward) + rationale."""

    exposure_id: str
    instrument: Instrument
    pair: CurrencyPair
    venue: str
    rationale: str
    created_at: datetime
    id: str = field(default_factory=new_id)


@dataclass(frozen=True, slots=True)
class Quote:
    """A time-bounded price offer for a declared exposure (TRD §4)."""

    exposure_id: str
    pair: CurrencyPair
    instrument: Instrument
    direction: Direction
    mid_rate: Rate  # the reference mid (spot S for CONVERT; forward F for HEDGE)
    all_in_rate: Rate
    spread_bps: Bps
    provider_fee_bps: Bps
    platform_fee_bps: Bps
    from_amount: Money  # what the agent pays / gives up
    to_amount: Money  # what the agent receives
    value_date: date  # settlement date (spot T+2 for CONVERT; delivery date for HEDGE)
    venue: str
    created_at: datetime
    expires_at: datetime
    spot_rate: Rate | None = None  # underlying spot S when this is a forward
    forward_points: Decimal | None = None  # F - S (signed) for HEDGE
    tenor_days: int | None = None  # days from spot value date to delivery (HEDGE)
    status: QuoteStatus = QuoteStatus.QUOTED
    id: str = field(default_factory=new_id)

    def is_expired(self, now: datetime) -> bool:
        return now > self.expires_at

    def with_status(self, status: QuoteStatus) -> Quote:
        return replace(self, status=status)


@dataclass(frozen=True, slots=True)
class Fill:
    """The result of an execution."""

    order_id: str
    executed_rate: Rate
    from_amount: Money
    to_amount: Money
    value_date: date
    venue: str
    filled_at: datetime


@dataclass(frozen=True, slots=True)
class CostComponent:
    """One line of a cost breakdown: an absolute amount and its size in basis points."""

    amount: Money
    bps: Bps


@dataclass(frozen=True, slots=True)
class CostAttribution:
    """Itemised cost breakdown; components must reconcile to ``all_in`` (invariant I2)."""

    order_id: str
    mid_reference_rate: Rate
    spread: CostComponent
    provider_fee: CostComponent
    platform_fee: CostComponent
    slippage: CostComponent
    rounding_residual: CostComponent
    all_in: CostComponent

    def components(self) -> list[CostComponent]:
        return [self.spread, self.provider_fee, self.platform_fee, self.slippage,
                self.rounding_residual]

    def reconciles(self) -> bool:
        total = sum((c.amount.amount_minor for c in self.components()), 0)
        return total == self.all_in.amount.amount_minor


@dataclass(slots=True)
class Order:
    """An execution attempt. Mutable lifecycle entity with guarded transitions (TRD §6)."""

    quote_id: str
    agent_id: str
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    status: OrderStatus = OrderStatus.CREATED
    fill: Fill | None = None
    id: str = field(default_factory=new_id)

    def transition(self, to: OrderStatus, now: datetime) -> None:
        if to not in _ORDER_TRANSITIONS[self.status]:
            raise ValueError(f"Illegal order transition {self.status} -> {to}")
        self.status = to
        self.updated_at = now

    def attach_fill(self, fill: Fill) -> None:
        self.fill = fill


@dataclass(frozen=True, slots=True)
class Position:
    """Signed net exposure per (agent, currency): positive = held, negative = owed/paid."""

    agent_id: str
    currency: str
    net_minor: int
    updated_at: datetime

    def as_money(self) -> Money:
        return Money(self.net_minor, self.currency)


@dataclass(frozen=True, slots=True)
class Policy:
    """Per-agent mandate, enforced server-side (FR-19/20). All caps are denominated in
    ``reference_currency``; a ``None`` cap means "no limit". An empty/``None`` allow-list means
    "all permitted". A permissive default (all ``None``) imposes no constraints."""

    agent_id: str
    reference_currency: str = "USD"
    max_single_ticket: Money | None = None  # cap on one ticket's notional, in reference ccy
    max_aggregate_net_exposure: Money | None = None  # cap on total net FX exposure, reference ccy
    approval_threshold: Money | None = None  # above this a human must sign off
    allowed_pairs: frozenset[str] | None = None  # None == all pairs allowed
    allowed_instruments: frozenset[Instrument] | None = None  # None == all instruments allowed


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An append-only record of a state change (TRD §4)."""

    agent_id: str
    event_type: str
    subject_type: str
    subject_id: str
    payload: dict[str, object]
    occurred_at: datetime
    correlation_id: str
    id: str = field(default_factory=new_id)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """Double-entry ledger posting (introduced in Phase 5; defined here for completeness)."""

    account: str
    debit: Money | None
    credit: Money | None
    ref_type: str
    ref_id: str
    posted_at: datetime
    id: str = field(default_factory=new_id)
