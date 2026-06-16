"""HTTP/JSON inbound adapter (FastAPI) — TRD §8.1.

A thin translation layer: it converts requests into use-case calls on :class:`NumeraService` and
domain objects into the shared DTOs (see :mod:`numera.adapters.dto`). It holds no business logic.
Domain errors are mapped to stable HTTP status codes + the structured error envelope (TRD §8.3).
"""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import __version__
from ..application.container import build_container
from ..application.use_cases import NumeraService
from ..domain.errors import DomainError
from ..domain.models import Direction, Timing
from ..domain.money import Money
from .dto import (
    AuditEventDTO,
    CostAttributionDTO,
    ExposureDecisionResponse,
    MarkToMarketResponse,
    OrderResponse,
    PolicyResponse,
    PositionsResponse,
    QuoteResponse,
    ReconciliationResponse,
    ReportResponse,
    exposure_decision_response,
    mark_to_market_response,
    order_response,
)
from .observability import configure_logging

# Domain error code -> HTTP status (TRD §8.3).
_ERROR_STATUS: dict[str, int] = {
    "UNKNOWN_CURRENCY": 422,
    "CURRENCY_MISMATCH": 422,
    "PAIR_NOT_ALLOWED": 422,
    "INVALID_VALUE_DATE": 422,
    "INVALID_EXPOSURE": 422,
    "INSTRUMENT_NOT_SUPPORTED": 422,
    "QUOTE_EXPIRED": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "NOT_FOUND": 404,
    "POLICY_LIMIT_EXCEEDED": 402,
    "RATE_UNAVAILABLE": 503,
    "VENUE_UNAVAILABLE": 503,
    "ATTRIBUTION_IMBALANCE": 500,
}


# -- HTTP request bodies (HTTP-specific; responses are shared in dto.py) ----------------------
class MoneyBody(BaseModel):
    amount_minor: int
    currency: str


class DeclareExposureRequest(BaseModel):
    given: MoneyBody
    target_currency: str
    direction: Direction
    timing: Timing
    value_date: date | None = None
    principal_id: str | None = None


class QuoteRequest(BaseModel):
    exposure_id: str


class ExecuteOrderRequest(BaseModel):
    quote_id: str


class PolicyRequest(BaseModel):
    reference_currency: str = "USD"
    max_single_ticket: MoneyBody | None = None
    max_aggregate_net_exposure: MoneyBody | None = None
    approval_threshold: MoneyBody | None = None
    allowed_pairs: list[str] | None = None
    allowed_instruments: list[str] | None = None


class ApproveRequest(BaseModel):
    approver: str = "human-operator"


def _money(b: MoneyBody | None) -> Money | None:
    return Money(b.amount_minor, b.currency) if b is not None else None


# --------------------------------------------------------------------------------------------
# App factory
# --------------------------------------------------------------------------------------------
def create_app(service: NumeraService | None = None) -> FastAPI:
    configure_logging()
    app = FastAPI(title="Numera", version=__version__,
                  summary="Agent-first FX / hedging micro-execution API (simulated venue).")
    svc = service or NumeraService(build_container())

    @app.exception_handler(DomainError)
    async def _domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        status = _ERROR_STATUS.get(exc.code, 400)
        return JSONResponse(
            status_code=status,
            content={"error": {"code": exc.code, "message": exc.message,
                               "details": exc.details, "recoverable": exc.recoverable}},
        )

    @app.get("/")
    def root() -> dict[str, str]:
        return {"name": "numera", "version": __version__}

    @app.post("/exposures", response_model=ExposureDecisionResponse, status_code=201)
    def declare_exposure(
        body: DeclareExposureRequest,
        x_agent_id: str = Header(default="agent-demo", alias="X-Agent-Id"),
    ) -> ExposureDecisionResponse:
        result = svc.declare_exposure(
            agent_id=x_agent_id,
            principal_id=body.principal_id or x_agent_id,
            given=Money(body.given.amount_minor, body.given.currency),
            target_currency=body.target_currency,
            direction=body.direction,
            timing=body.timing,
            value_date=body.value_date,
        )
        return exposure_decision_response(result.exposure, result.decision)

    @app.post("/quotes", response_model=QuoteResponse, status_code=201)
    def request_quote(body: QuoteRequest) -> QuoteResponse:
        return QuoteResponse.of(svc.request_quote(exposure_id=body.exposure_id))

    @app.post("/orders", response_model=OrderResponse, status_code=201)
    def execute_order(
        body: ExecuteOrderRequest,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        x_agent_id: str = Header(default="agent-demo", alias="X-Agent-Id"),
    ) -> OrderResponse:
        view = svc.execute_order(
            agent_id=x_agent_id, quote_id=body.quote_id, idempotency_key=idempotency_key
        )
        return order_response(view.order, view.fill, view.attribution)

    @app.get("/orders/{order_id}", response_model=OrderResponse)
    def get_order(order_id: str) -> OrderResponse:
        view = svc.get_order(order_id)
        return order_response(view.order, view.fill, view.attribution)

    @app.get("/orders/{order_id}/cost", response_model=CostAttributionDTO)
    def get_cost_breakdown(order_id: str) -> CostAttributionDTO:
        return CostAttributionDTO.of(svc.get_cost_breakdown(order_id))

    @app.get("/orders/{order_id}/mtm", response_model=MarkToMarketResponse)
    def mark_to_market(order_id: str) -> MarkToMarketResponse:
        v = svc.get_mark_to_market(order_id)
        return mark_to_market_response(
            order_id=v.order_id, as_of=v.as_of, current_mid=v.current_mid,
            locked_amount=v.locked_amount, current_value=v.current_value,
            unrealized_pnl=v.unrealized_pnl,
        )

    @app.post("/orders/{order_id}/approve", response_model=OrderResponse)
    def approve_order(order_id: str, body: ApproveRequest) -> OrderResponse:
        view = svc.approve_order(order_id=order_id, approver=body.approver)
        return order_response(view.order, view.fill, view.attribution)

    @app.put("/policies/{agent_id}", response_model=PolicyResponse)
    def set_policy(agent_id: str, body: PolicyRequest) -> PolicyResponse:
        policy = svc.set_policy(
            agent_id=agent_id, reference_currency=body.reference_currency,
            max_single_ticket=_money(body.max_single_ticket),
            max_aggregate_net_exposure=_money(body.max_aggregate_net_exposure),
            approval_threshold=_money(body.approval_threshold),
            allowed_pairs=body.allowed_pairs, allowed_instruments=body.allowed_instruments,
        )
        return PolicyResponse.of(policy)

    @app.get("/positions", response_model=PositionsResponse)
    def get_positions(
        x_agent_id: str = Header(default="agent-demo", alias="X-Agent-Id"),
    ) -> PositionsResponse:
        return PositionsResponse.of(svc.get_positions(x_agent_id))

    @app.get("/audit", response_model=list[AuditEventDTO])
    def get_audit(
        agent_id: str | None = None, subject_id: str | None = None,
        event_type: str | None = None, limit: int | None = None,
    ) -> list[AuditEventDTO]:
        events = svc.get_audit(agent_id=agent_id, subject_id=subject_id,
                               event_type=event_type, limit=limit)
        return [AuditEventDTO.of(e) for e in events]

    @app.get("/orders/{order_id}/reconcile", response_model=ReconciliationResponse)
    def reconcile(order_id: str) -> ReconciliationResponse:
        return ReconciliationResponse.of(svc.reconcile(order_id))

    @app.get("/report", response_model=ReportResponse)
    def get_report(
        x_agent_id: str = Header(default="agent-demo", alias="X-Agent-Id"),
    ) -> ReportResponse:
        return ReportResponse.of(svc.get_report(x_agent_id))

    @app.get("/metrics")
    def metrics() -> dict[str, object]:
        return svc.metrics_snapshot()

    return app


# Convenience for `uvicorn numera.adapters.api:app`
app = create_app()
