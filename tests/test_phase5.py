"""Audit + reconstruction (SM-4), ledger reconciliation (FR-25), reporting (FR-18), metrics."""

from __future__ import annotations

from fastapi.testclient import TestClient

from numera.application.use_cases import NumeraService, OrderView
from numera.domain.models import Direction, Timing
from numera.domain.money import Money


def _fill(service: NumeraService, agent: str, amount: str = "1000", key: str = "k") -> OrderView:
    decl = service.declare_exposure(
        agent_id=agent, principal_id=agent, given=Money.from_major(amount, "USD"),
        target_currency="INR", direction=Direction.HAVE, timing=Timing.SPOT,
    )
    quote = service.request_quote(exposure_id=decl.exposure.id)
    return service.execute_order(agent_id=agent, quote_id=quote.id, idempotency_key=key)


def test_audit_trail_reconstructs_the_order(service: NumeraService) -> None:
    """SM-4: the full lifecycle is reconstructable from the audit trail alone."""
    view = _fill(service, "aud")
    by_agent = {e.event_type for e in service.get_audit(agent_id="aud")}
    assert {"exposure.declared", "exposure.decided", "quote.created", "order.filled"} <= by_agent
    # The order-scoped slice carries the fill event with its economics.
    order_events = service.get_audit(subject_id=view.order.id)
    filled = [e for e in order_events if e.event_type == "order.filled"]
    assert len(filled) == 1
    assert "executed_rate" in filled[0].payload and "all_in_cost" in filled[0].payload


def test_ledger_reconciles(service: NumeraService) -> None:
    view = _fill(service, "rec")
    recon = service.reconcile(view.order.id)
    assert recon.venue_state == "FILLED"
    assert recon.balanced is True
    assert {line.currency for line in recon.lines} == {"USD", "INR"}
    assert all(line.balanced for line in recon.lines)  # debits == credits per currency


def test_report_aggregates_realized_cost(service: NumeraService) -> None:
    _fill(service, "rep", amount="1000", key="r1")
    _fill(service, "rep", amount="500", key="r2")
    report = service.get_report("rep")
    assert report.reference_currency == "USD"
    assert report.order_counts.get("FILLED") == 2
    assert report.realized_cost.amount_minor > 0  # spread+fees on two fills
    assert report.outstanding_exposure.currency == "USD"


def test_metrics_record_activity(service: NumeraService) -> None:
    _fill(service, "met")
    counters = service.metrics_snapshot()["counters"]
    assert counters["quotes_created"] >= 1
    assert counters["orders_filled"] >= 1
    latency = service.metrics_snapshot()["latency"]
    assert "execute_latency_ms" in latency


def test_http_audit_reconcile_report_metrics(client: TestClient) -> None:
    decl = client.post("/exposures", json={
        "given": {"amount_minor": 100000, "currency": "USD"},
        "target_currency": "INR", "direction": "HAVE", "timing": "SPOT",
    })
    quote = client.post("/quotes", json={"exposure_id": decl.json()["exposure"]["id"]}).json()
    order = client.post("/orders", json={"quote_id": quote["id"]},
                        headers={"Idempotency-Key": "p5"}).json()

    audit = client.get("/audit", params={"agent_id": "agent-demo"})
    assert audit.status_code == 200 and len(audit.json()) >= 4

    recon = client.get(f"/orders/{order['id']}/reconcile")
    assert recon.status_code == 200 and recon.json()["balanced"] is True

    report = client.get("/report", headers={"X-Agent-Id": "agent-demo"})
    assert report.status_code == 200 and report.json()["order_counts"]["FILLED"] >= 1

    metrics = client.get("/metrics")
    assert metrics.status_code == 200 and metrics.json()["counters"]["orders_filled"] >= 1
