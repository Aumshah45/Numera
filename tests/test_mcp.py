"""MCP adapter: tools work via an in-memory session, and parity with HTTP (FR-27/28, SM-6)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session

from numera.adapters.api import create_app
from numera.adapters.calendar import WeekendCalendar
from numera.adapters.clock import FixedClock
from numera.adapters.mcp_server import create_mcp_server
from numera.adapters.rates import FlatRateCurve, SimRateFeed
from numera.adapters.repositories import Repositories
from numera.adapters.venue import SimulatedVenue
from numera.application.container import Container
from numera.application.use_cases import NumeraService
from numera.domain.rate import Bps
from numera.domain.services import CostAttributor, DecisionEngine, FeeConfig, MarkToMarket, Pricer

EXPOSURE_ARGS = {
    "given_amount_minor": 180000, "given_currency": "USD",
    "target_currency": "INR", "direction": "HAVE", "timing": "SPOT",
}


def _det_service() -> NumeraService:
    """A deterministic service: fixed clock + simulated feed + no slippage."""
    cal = WeekendCalendar()
    return NumeraService(Container(
        clock=FixedClock(datetime(2026, 6, 15, 12, 0, tzinfo=UTC)),
        rate_feed=SimRateFeed(),
        venue=SimulatedVenue(cal, spread_bps=Bps.of("25"), provider_fee_bps=Bps.of("10")),
        decision_engine=DecisionEngine(),
        pricer=Pricer(FeeConfig(platform_fee_bps=Bps.of("5"), quote_ttl_seconds=120),
                      rate_curve=FlatRateCurve()),
        cost_attributor=CostAttributor(),
        mark_to_market=MarkToMarket(),
        repos=Repositories(),
    ))


async def _mcp_flow(service: NumeraService) -> dict[str, Any]:
    server = create_mcp_server(service)
    async with create_connected_server_and_client_session(server) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        decl = await client.call_tool("declare_exposure", EXPOSURE_ARGS)
        exposure_id = decl.structuredContent["exposure"]["id"]
        quote = await client.call_tool("get_quote", {"exposure_id": exposure_id})
        quote_id = quote.structuredContent["id"]
        order = await client.call_tool(
            "execute_hedge", {"quote_id": quote_id, "idempotency_key": "mk-1"}
        )
        order_id = order.structuredContent["id"]
        cost = await client.call_tool("get_cost_breakdown", {"order_id": order_id})
        bad = await client.call_tool(
            "execute_hedge", {"quote_id": "does-not-exist", "idempotency_key": "x"}
        )
        return {
            "tools": tools,
            "quote": quote.structuredContent,
            "order": order.structuredContent,
            "cost": cost.structuredContent,
            "error_is_error": bad.isError,
        }


def test_mcp_tools_available_and_flow() -> None:
    result = asyncio.run(_mcp_flow(_det_service()))
    assert result["tools"] == {
        "declare_exposure", "get_quote", "execute_hedge", "get_order", "get_cost_breakdown",
        "mark_to_market", "set_policy", "approve_order", "get_position",
        "get_audit", "reconcile_order", "get_report",
    }
    assert result["order"]["status"] == "FILLED"
    ca = result["order"]["cost_attribution"]
    components = ["spread", "provider_fee", "platform_fee", "slippage", "rounding_residual"]
    total = sum(ca[c]["amount"]["amount_minor"] for c in components)
    assert total == ca["all_in"]["amount"]["amount_minor"]
    # get_cost_breakdown returns the same all-in figure as the embedded attribution.
    assert result["cost"]["all_in"] == ca["all_in"]
    # An invalid execution surfaces as an MCP tool error.
    assert result["error_is_error"] is True


def test_http_and_mcp_parity() -> None:
    """Identical inputs through HTTP and MCP yield identical computed economics (SM-6)."""
    mcp = asyncio.run(_mcp_flow(_det_service()))

    client = TestClient(create_app(_det_service()))
    decl = client.post("/exposures", json={
        "given": {"amount_minor": 180000, "currency": "USD"},
        "target_currency": "INR", "direction": "HAVE", "timing": "SPOT",
    })
    quote = client.post("/quotes", json={"exposure_id": decl.json()["exposure"]["id"]}).json()
    order = client.post("/orders", json={"quote_id": quote["id"]},
                        headers={"Idempotency-Key": "mk-1"}).json()

    # Quotes match on every field except the random/linking ids.
    def _strip_quote(q: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in q.items() if k not in ("id", "exposure_id")}

    assert _strip_quote(quote) == _strip_quote(mcp["quote"])
    # Cost attribution matches except the order id it references.
    http_ca = {k: v for k, v in order["cost_attribution"].items() if k != "order_id"}
    mcp_ca = {k: v for k, v in mcp["order"]["cost_attribution"].items() if k != "order_id"}
    assert http_ca == mcp_ca
