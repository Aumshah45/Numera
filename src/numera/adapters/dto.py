"""Shared response DTOs (Pydantic) used by BOTH the HTTP and MCP adapters.

Keeping the wire representation in one place guarantees the two surfaces serialize identically
(FR-28 parity). Each DTO has an ``of(...)`` constructor mapping a domain object to the DTO.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from ..application.use_cases import PositionsView, ReconciliationView, ReportView
from ..domain.models import (
    AuditEvent,
    CostAttribution,
    CostComponent,
    Decision,
    Direction,
    Exposure,
    Fill,
    Order,
    Policy,
    Quote,
    Timing,
)
from ..domain.money import Money
from ..domain.rate import Rate


class MoneyDTO(BaseModel):
    amount_minor: int
    currency: str

    @classmethod
    def of(cls, m: Money) -> MoneyDTO:
        return cls(amount_minor=m.amount_minor, currency=m.currency)


class ExposureDTO(BaseModel):
    id: str
    agent_id: str
    principal_id: str
    given: MoneyDTO
    target_currency: str
    direction: Direction
    timing: Timing
    value_date: date | None
    status: str

    @classmethod
    def of(cls, e: Exposure) -> ExposureDTO:
        return cls(
            id=e.id, agent_id=e.agent_id, principal_id=e.principal_id, given=MoneyDTO.of(e.given),
            target_currency=e.target_currency, direction=e.direction, timing=e.timing,
            value_date=e.value_date, status=str(e.status),
        )


class DecisionDTO(BaseModel):
    instrument: str
    pair: str
    venue: str
    rationale: str

    @classmethod
    def of(cls, d: Decision) -> DecisionDTO:
        return cls(instrument=str(d.instrument), pair=str(d.pair), venue=d.venue,
                   rationale=d.rationale)


class ExposureDecisionResponse(BaseModel):
    exposure: ExposureDTO
    decision: DecisionDTO


class QuoteResponse(BaseModel):
    id: str
    exposure_id: str
    pair: str
    instrument: str
    direction: str
    mid_rate: str  # reference mid: spot S for CONVERT, forward F for HEDGE
    all_in_rate: str
    spread_bps: str
    provider_fee_bps: str
    platform_fee_bps: str
    from_amount: MoneyDTO
    to_amount: MoneyDTO
    value_date: date
    venue: str
    expires_at: datetime
    status: str
    spot_rate: str | None = None
    forward_points: str | None = None
    tenor_days: int | None = None

    @classmethod
    def of(cls, q: Quote) -> QuoteResponse:
        return cls(
            id=q.id, exposure_id=q.exposure_id, pair=str(q.pair), instrument=str(q.instrument),
            direction=str(q.direction), mid_rate=str(q.mid_rate), all_in_rate=str(q.all_in_rate),
            spread_bps=str(q.spread_bps.value), provider_fee_bps=str(q.provider_fee_bps.value),
            platform_fee_bps=str(q.platform_fee_bps.value), from_amount=MoneyDTO.of(q.from_amount),
            to_amount=MoneyDTO.of(q.to_amount), value_date=q.value_date, venue=q.venue,
            expires_at=q.expires_at, status=str(q.status),
            spot_rate=str(q.spot_rate) if q.spot_rate is not None else None,
            forward_points=format(q.forward_points, "f") if q.forward_points is not None else None,
            tenor_days=q.tenor_days,
        )


class CostComponentDTO(BaseModel):
    amount: MoneyDTO
    bps: str

    @classmethod
    def of(cls, c: CostComponent) -> CostComponentDTO:
        return cls(amount=MoneyDTO.of(c.amount), bps=str(c.bps.value))


class CostAttributionDTO(BaseModel):
    order_id: str
    mid_reference_rate: str
    spread: CostComponentDTO
    provider_fee: CostComponentDTO
    platform_fee: CostComponentDTO
    slippage: CostComponentDTO
    rounding_residual: CostComponentDTO
    all_in: CostComponentDTO

    @classmethod
    def of(cls, a: CostAttribution) -> CostAttributionDTO:
        return cls(
            order_id=a.order_id, mid_reference_rate=str(a.mid_reference_rate),
            spread=CostComponentDTO.of(a.spread), provider_fee=CostComponentDTO.of(a.provider_fee),
            platform_fee=CostComponentDTO.of(a.platform_fee),
            slippage=CostComponentDTO.of(a.slippage),
            rounding_residual=CostComponentDTO.of(a.rounding_residual),
            all_in=CostComponentDTO.of(a.all_in),
        )


class FillDTO(BaseModel):
    executed_rate: str
    from_amount: MoneyDTO
    to_amount: MoneyDTO
    value_date: date
    venue: str
    filled_at: datetime

    @classmethod
    def of(cls, f: Fill) -> FillDTO:
        return cls(executed_rate=str(f.executed_rate), from_amount=MoneyDTO.of(f.from_amount),
                   to_amount=MoneyDTO.of(f.to_amount), value_date=f.value_date, venue=f.venue,
                   filled_at=f.filled_at)


class OrderResponse(BaseModel):
    id: str
    status: str
    quote_id: str
    agent_id: str
    fill: FillDTO | None = None
    cost_attribution: CostAttributionDTO | None = None


class MarkToMarketResponse(BaseModel):
    order_id: str
    as_of: datetime
    current_mid: str
    locked_amount: MoneyDTO
    current_value: MoneyDTO
    unrealized_pnl: MoneyDTO


class PolicyResponse(BaseModel):
    agent_id: str
    reference_currency: str
    max_single_ticket: MoneyDTO | None
    max_aggregate_net_exposure: MoneyDTO | None
    approval_threshold: MoneyDTO | None
    allowed_pairs: list[str] | None
    allowed_instruments: list[str] | None

    @classmethod
    def of(cls, p: Policy) -> PolicyResponse:
        return cls(
            agent_id=p.agent_id, reference_currency=p.reference_currency,
            max_single_ticket=MoneyDTO.of(p.max_single_ticket) if p.max_single_ticket else None,
            max_aggregate_net_exposure=(
                MoneyDTO.of(p.max_aggregate_net_exposure)
                if p.max_aggregate_net_exposure else None
            ),
            approval_threshold=MoneyDTO.of(p.approval_threshold) if p.approval_threshold else None,
            allowed_pairs=sorted(p.allowed_pairs) if p.allowed_pairs is not None else None,
            allowed_instruments=(
                sorted(str(i) for i in p.allowed_instruments)
                if p.allowed_instruments is not None else None
            ),
        )


class PositionLineDTO(BaseModel):
    currency: str
    net: MoneyDTO
    value_in_reference: MoneyDTO


class PositionsResponse(BaseModel):
    agent_id: str
    reference_currency: str
    positions: list[PositionLineDTO]
    aggregate_net_exposure: MoneyDTO

    @classmethod
    def of(cls, v: PositionsView) -> PositionsResponse:
        return cls(
            agent_id=v.agent_id, reference_currency=v.reference_currency,
            positions=[
                PositionLineDTO(currency=line.currency, net=MoneyDTO.of(line.net),
                                value_in_reference=MoneyDTO.of(line.value_in_reference))
                for line in v.positions
            ],
            aggregate_net_exposure=MoneyDTO.of(v.aggregate_net_exposure),
        )


class AuditEventDTO(BaseModel):
    id: str
    agent_id: str
    event_type: str
    subject_type: str
    subject_id: str
    occurred_at: datetime
    correlation_id: str
    payload: dict[str, object]

    @classmethod
    def of(cls, e: AuditEvent) -> AuditEventDTO:
        return cls(id=e.id, agent_id=e.agent_id, event_type=e.event_type,
                   subject_type=e.subject_type, subject_id=e.subject_id,
                   occurred_at=e.occurred_at, correlation_id=e.correlation_id, payload=e.payload)


class ReconciliationLineDTO(BaseModel):
    currency: str
    debit: MoneyDTO
    credit: MoneyDTO
    balanced: bool


class ReconciliationResponse(BaseModel):
    order_id: str
    venue_state: str
    balanced: bool
    lines: list[ReconciliationLineDTO]

    @classmethod
    def of(cls, v: ReconciliationView) -> ReconciliationResponse:
        return cls(
            order_id=v.order_id, venue_state=v.venue_state, balanced=v.balanced,
            lines=[
                ReconciliationLineDTO(currency=line.currency, debit=MoneyDTO.of(line.debit),
                                      credit=MoneyDTO.of(line.credit), balanced=line.balanced)
                for line in v.lines
            ],
        )


class ReportResponse(BaseModel):
    agent_id: str
    reference_currency: str
    realized_cost: MoneyDTO
    outstanding_exposure: MoneyDTO
    order_counts: dict[str, int]

    @classmethod
    def of(cls, v: ReportView) -> ReportResponse:
        return cls(agent_id=v.agent_id, reference_currency=v.reference_currency,
                   realized_cost=MoneyDTO.of(v.realized_cost),
                   outstanding_exposure=MoneyDTO.of(v.outstanding_exposure),
                   order_counts=v.order_counts)


# -- composite builders (domain -> wire), shared by HTTP and MCP -----------------------------
def mark_to_market_response(
    *, order_id: str, as_of: datetime, current_mid: Rate, locked_amount: Money,
    current_value: Money, unrealized_pnl: Money,
) -> MarkToMarketResponse:
    return MarkToMarketResponse(
        order_id=order_id, as_of=as_of, current_mid=str(current_mid),
        locked_amount=MoneyDTO.of(locked_amount), current_value=MoneyDTO.of(current_value),
        unrealized_pnl=MoneyDTO.of(unrealized_pnl),
    )


def exposure_decision_response(
    exposure: Exposure, decision: Decision
) -> ExposureDecisionResponse:
    return ExposureDecisionResponse(
        exposure=ExposureDTO.of(exposure), decision=DecisionDTO.of(decision)
    )


def order_response(
    order: Order, fill: Fill | None, attribution: CostAttribution | None
) -> OrderResponse:
    return OrderResponse(
        id=order.id, status=str(order.status), quote_id=order.quote_id, agent_id=order.agent_id,
        fill=FillDTO.of(fill) if fill else None,
        cost_attribution=CostAttributionDTO.of(attribution) if attribution else None,
    )
