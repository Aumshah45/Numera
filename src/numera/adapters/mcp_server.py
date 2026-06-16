"""MCP inbound adapter — exposes Numera as agent-native tools (TRD §8.2, FR-27/28).

This is a thin surface over the *same* :class:`NumeraService` use-cases as the HTTP API, returning
the *same* shared DTOs (:mod:`numera.adapters.dto`) — so the two surfaces cannot diverge (parity,
SM-6). Domain errors are translated into MCP tool errors carrying the stable error code.

Run as a stdio MCP server:  ``numera-mcp``  (or ``python -m numera.adapters.mcp_server``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

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


@contextmanager
def _translate_errors() -> Iterator[None]:
    """Surface domain errors as MCP tool errors that carry the stable error code."""
    try:
        yield
    except DomainError as exc:
        raise ToolError(f"[{exc.code}] {exc.message}") from exc


def create_mcp_server(service: NumeraService | None = None) -> FastMCP:
    svc = service or NumeraService(build_container())
    mcp = FastMCP(
        "numera",
        instructions=(
            "Agent-first FX / hedging. Declare a currency exposure, request a quote, then "
            "execute it; every fill comes with a full, reconciling cost attribution. Execution "
            "is simulated — no real money moves."
        ),
    )

    @mcp.tool()
    def declare_exposure(
        given_amount_minor: int,
        given_currency: str,
        target_currency: str,
        direction: Direction,
        timing: Timing,
        value_date: date | None = None,
        agent_id: str = "agent-demo",
        principal_id: str | None = None,
    ) -> ExposureDecisionResponse:
        """Declare a currency exposure; returns the exposure and the convert/hedge decision."""
        with _translate_errors():
            result = svc.declare_exposure(
                agent_id=agent_id,
                principal_id=principal_id or agent_id,
                given=Money(given_amount_minor, given_currency),
                target_currency=target_currency,
                direction=direction,
                timing=timing,
                value_date=value_date,
            )
            return exposure_decision_response(result.exposure, result.decision)

    @mcp.tool()
    def get_quote(exposure_id: str) -> QuoteResponse:
        """Request a time-bounded quote (with TTL) for a declared exposure."""
        with _translate_errors():
            return QuoteResponse.of(svc.request_quote(exposure_id=exposure_id))

    @mcp.tool()
    def execute_hedge(
        quote_id: str, idempotency_key: str, agent_id: str = "agent-demo"
    ) -> OrderResponse:
        """Execute an accepted quote. Idempotent on ``idempotency_key`` (safe to retry)."""
        with _translate_errors():
            view = svc.execute_order(
                agent_id=agent_id, quote_id=quote_id, idempotency_key=idempotency_key
            )
            return order_response(view.order, view.fill, view.attribution)

    @mcp.tool()
    def get_order(order_id: str) -> OrderResponse:
        """Fetch an order with its fill and cost attribution."""
        with _translate_errors():
            view = svc.get_order(order_id)
            return order_response(view.order, view.fill, view.attribution)

    @mcp.tool()
    def get_cost_breakdown(order_id: str) -> CostAttributionDTO:
        """Fetch the itemised cost breakdown for a filled order (reconciles to the all-in cost)."""
        with _translate_errors():
            return CostAttributionDTO.of(svc.get_cost_breakdown(order_id))

    @mcp.tool()
    def mark_to_market(order_id: str) -> MarkToMarketResponse:
        """Revalue a filled order at the current mid; returns unrealized P&L (target currency)."""
        with _translate_errors():
            v = svc.get_mark_to_market(order_id)
            return mark_to_market_response(
                order_id=v.order_id, as_of=v.as_of, current_mid=v.current_mid,
                locked_amount=v.locked_amount, current_value=v.current_value,
                unrealized_pnl=v.unrealized_pnl,
            )

    @mcp.tool()
    def set_policy(
        agent_id: str = "agent-demo",
        reference_currency: str = "USD",
        max_single_ticket_minor: int | None = None,
        max_aggregate_net_exposure_minor: int | None = None,
        approval_threshold_minor: int | None = None,
        allowed_pairs: list[str] | None = None,
        allowed_instruments: list[str] | None = None,
    ) -> PolicyResponse:
        """Set an agent's risk mandate. Caps are minor units in ``reference_currency``."""
        def _m(minor: int | None) -> Money | None:
            return Money(minor, reference_currency) if minor is not None else None

        with _translate_errors():
            policy = svc.set_policy(
                agent_id=agent_id, reference_currency=reference_currency,
                max_single_ticket=_m(max_single_ticket_minor),
                max_aggregate_net_exposure=_m(max_aggregate_net_exposure_minor),
                approval_threshold=_m(approval_threshold_minor),
                allowed_pairs=allowed_pairs, allowed_instruments=allowed_instruments,
            )
            return PolicyResponse.of(policy)

    @mcp.tool()
    def approve_order(order_id: str, approver: str = "human-operator") -> OrderResponse:
        """Sign off on an order parked above the approval threshold, then execute it."""
        with _translate_errors():
            view = svc.approve_order(order_id=order_id, approver=approver)
            return order_response(view.order, view.fill, view.attribution)

    @mcp.tool()
    def get_position(agent_id: str = "agent-demo") -> PositionsResponse:
        """Net FX exposure per currency for an agent, with aggregate valued in the reference ccy."""
        with _translate_errors():
            return PositionsResponse.of(svc.get_positions(agent_id))

    @mcp.tool()
    def get_audit(
        agent_id: str | None = None, subject_id: str | None = None,
        event_type: str | None = None, limit: int | None = None,
    ) -> list[AuditEventDTO]:
        """Query the append-only audit trail (filter by agent, subject, event type)."""
        with _translate_errors():
            events = svc.get_audit(agent_id=agent_id, subject_id=subject_id,
                                   event_type=event_type, limit=limit)
            return [AuditEventDTO.of(e) for e in events]

    @mcp.tool()
    def reconcile_order(order_id: str) -> ReconciliationResponse:
        """Reconcile an order: venue status + double-entry ledger balance per currency."""
        with _translate_errors():
            return ReconciliationResponse.of(svc.reconcile(order_id))

    @mcp.tool()
    def get_report(agent_id: str = "agent-demo") -> ReportResponse:
        """Per-agent report: realized cost, outstanding exposure, and order counts by status."""
        with _translate_errors():
            return ReportResponse.of(svc.get_report(agent_id))

    return mcp


def main() -> None:
    create_mcp_server().run()


if __name__ == "__main__":
    main()
