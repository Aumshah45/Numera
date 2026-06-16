"""Outbound ports (interfaces the core depends on) — TRD §10, ARCHITECTURE §2.

The application/domain depend only on these Protocols; concrete adapters live in
``numera.adapters``. The ``ExecutionVenue`` port is the regulated seam (FR-29/30): a simulator
implements it now, a licensed partner could later — with no change above this line.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from .domain.currency import CurrencyPair
from .domain.models import (
    AuditEvent,
    CostAttribution,
    Decision,
    Exposure,
    Instrument,
    LedgerEntry,
    Order,
    Policy,
    Position,
    Quote,
)
from .domain.money import Money
from .domain.rate import Bps, Rate


# --------------------------------------------------------------------------------------------
# Infrastructure ports
# --------------------------------------------------------------------------------------------
class Clock(Protocol):
    """Source of the current time. Injected so tests can be deterministic (NFR-8)."""

    def now(self) -> datetime: ...


class BusinessCalendar(Protocol):
    """Business-day rules used for settlement/value-date calculation (TRD §3.2)."""

    def is_business_day(self, day: date) -> bool: ...

    def add_business_days(self, start: date, n: int) -> date: ...


class RateFeed(Protocol):
    """Supplies the real mid-market rate for a pair (QUOTE per BASE)."""

    def get_mid(self, pair: CurrencyPair) -> Rate: ...


class RateCurve(Protocol):
    """Per-currency interest rates for forward pricing (Phase 3; flat/simulated in v1)."""

    def rate(self, currency: str, tenor_days: int) -> Decimal: ...


# --------------------------------------------------------------------------------------------
# Execution venue (the regulated seam)
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class VenueQuote:
    """Indicative venue economics: the spread/fee charged, the settlement date, and (for a
    forward) the spot value date used to compute the tenor."""

    spread_bps: Bps
    provider_fee_bps: Bps
    value_date: date
    spot_value_date: date


@dataclass(frozen=True, slots=True)
class VenueFill:
    """The venue's execution result (no real money moves in the simulator).

    Carries both legs because slippage may move whichever leg is computed (the received leg for a
    HAVE conversion, the paid leg for an OWE hedge)."""

    executed_rate: Rate
    from_amount: Money
    to_amount: Money
    value_date: date


@dataclass(frozen=True, slots=True)
class VenueStatus:
    ref: str
    state: str


class ExecutionVenue(Protocol):
    """The execution seam. Any implementation must pass the venue contract test suite (FR-30)."""

    name: str

    def quote(
        self,
        *,
        pair: CurrencyPair,
        from_amount: Money,
        mid_rate: Rate,
        now: datetime,
        instrument: Instrument,
        requested_value_date: date | None = None,
    ) -> VenueQuote: ...

    def execute(self, *, quote: Quote, idempotency_key: str) -> VenueFill: ...

    def status(self, ref: str) -> VenueStatus: ...


# --------------------------------------------------------------------------------------------
# Repository ports (TRD §9/§10). Implementations: in-memory now; SQLAlchemy later.
# --------------------------------------------------------------------------------------------
class ExposureRepository(Protocol):
    def add(self, exposure: Exposure) -> None: ...

    def get(self, exposure_id: str) -> Exposure: ...

    def update(self, exposure: Exposure) -> None: ...


class DecisionRepository(Protocol):
    def add(self, decision: Decision) -> None: ...

    def get_by_exposure(self, exposure_id: str) -> Decision: ...


class QuoteRepository(Protocol):
    def add(self, quote: Quote) -> None: ...

    def get(self, quote_id: str) -> Quote: ...

    def update(self, quote: Quote) -> None: ...


class OrderRepository(Protocol):
    def add(self, order: Order) -> None: ...

    def get(self, order_id: str) -> Order: ...

    def update(self, order: Order) -> None: ...

    def list(self, agent_id: str) -> list[Order]: ...


class AttributionRepository(Protocol):
    def add(self, attribution: CostAttribution) -> None: ...

    def get_by_order(self, order_id: str) -> CostAttribution: ...


class AuditRepository(Protocol):
    def append(self, event: AuditEvent) -> None: ...

    def list(
        self, *, agent_id: str | None = None, subject_id: str | None = None,
        event_type: str | None = None, limit: int | None = None,
    ) -> list[AuditEvent]: ...


class IdempotencyStore(Protocol):
    """Maps ``(agent_id, idempotency_key)`` to the order id it produced (FR-11)."""

    def get(self, agent_id: str, key: str) -> str | None: ...

    def put(self, agent_id: str, key: str, order_id: str) -> None: ...


class PolicyRepository(Protocol):
    def get(self, agent_id: str) -> Policy | None: ...

    def put(self, policy: Policy) -> None: ...


class PositionRepository(Protocol):
    def get(self, agent_id: str, currency: str) -> Position | None: ...

    def upsert(self, position: Position) -> None: ...

    def list(self, agent_id: str) -> list[Position]: ...


class LedgerRepository(Protocol):
    """Append-only double-entry ledger (FR-25). Postings balance per currency per operation."""

    def add(self, entry: LedgerEntry) -> None: ...

    def list_for_ref(self, ref_id: str) -> list[LedgerEntry]: ...
