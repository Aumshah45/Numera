"""Policy guardrails: pure engine, server-side enforcement, approval, positions (FR-16/19-22)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from numera.application.use_cases import NumeraService
from numera.domain.currency import CurrencyPair
from numera.domain.errors import InstrumentNotAllowed, PairNotAllowed, PolicyLimitExceeded
from numera.domain.models import Direction, Instrument, OrderStatus, Policy, Timing
from numera.domain.money import Money
from numera.domain.services import PolicyEngine, PolicyOutcome

USD = "USD"
PAIR = CurrencyPair("USD", "INR")


def _usd(major: str) -> Money:
    return Money.from_major(major, USD)


# ---- pure PolicyEngine (FR-21) -------------------------------------------------------------
def _evaluate(policy: Policy, *, ticket: str, aggregate: str = "0", instrument=Instrument.CONVERT,
              pair: CurrencyPair = PAIR, approved: bool = False):
    return PolicyEngine().evaluate(
        pair=pair, instrument=instrument, ticket_notional=_usd(ticket),
        projected_aggregate=_usd(aggregate), policy=policy, approved=approved,
    )


def test_permissive_policy_allows() -> None:
    assert _evaluate(Policy(agent_id="a"), ticket="100000").outcome is PolicyOutcome.ALLOW


def test_single_ticket_cap_rejects() -> None:
    p = Policy(agent_id="a", max_single_ticket=_usd("1000"))
    r = _evaluate(p, ticket="1500")
    assert r.outcome is PolicyOutcome.REJECT and r.code == "POLICY_LIMIT_EXCEEDED"


def test_aggregate_cap_rejects() -> None:
    p = Policy(agent_id="a", max_aggregate_net_exposure=_usd("1000"))
    assert _evaluate(p, ticket="100", aggregate="1200").outcome is PolicyOutcome.REJECT


def test_approval_threshold_then_waived_when_approved() -> None:
    p = Policy(agent_id="a", approval_threshold=_usd("500"))
    assert _evaluate(p, ticket="800").outcome is PolicyOutcome.REQUIRES_APPROVAL
    assert _evaluate(p, ticket="800", approved=True).outcome is PolicyOutcome.ALLOW


def test_approval_does_not_waive_hard_caps() -> None:
    p = Policy(agent_id="a", max_single_ticket=_usd("1000"), approval_threshold=_usd("500"))
    assert _evaluate(p, ticket="1500", approved=True).outcome is PolicyOutcome.REJECT


def test_allow_lists() -> None:
    p = Policy(agent_id="a", allowed_pairs=frozenset({"EUR/USD"}),
               allowed_instruments=frozenset({Instrument.CONVERT}))
    assert _evaluate(p, ticket="10").code == "PAIR_NOT_ALLOWED"
    p2 = Policy(agent_id="a", allowed_instruments=frozenset({Instrument.CONVERT}))
    assert _evaluate(p2, ticket="10", instrument=Instrument.HEDGE).code == "INSTRUMENT_NOT_ALLOWED"


# ---- server-side enforcement via the service ----------------------------------------------
def _spot_quote(service: NumeraService, agent: str, amount: str,
                base: str = "USD", target: str = "INR") -> str:
    decl = service.declare_exposure(
        agent_id=agent, principal_id=agent, given=Money.from_major(amount, base),
        target_currency=target, direction=Direction.HAVE, timing=Timing.SPOT,
    )
    return service.request_quote(exposure_id=decl.exposure.id).id


def test_over_single_ticket_is_rejected(service: NumeraService) -> None:
    service.set_policy(agent_id="a1", max_single_ticket=_usd("1000"))
    qid = _spot_quote(service, "a1", "1800")
    with pytest.raises(PolicyLimitExceeded):
        service.execute_order(agent_id="a1", quote_id=qid, idempotency_key="k")


def test_sequential_aggregate_cannot_be_exceeded(service: NumeraService) -> None:
    """SM-3: a sequence of in-isolation-legal tickets cannot breach the aggregate cap."""
    service.set_policy(agent_id="agg", max_aggregate_net_exposure=_usd("1000"))
    first = service.execute_order(agent_id="agg", quote_id=_spot_quote(service, "agg", "800"),
                                  idempotency_key="agg-1")
    assert first.order.status is OrderStatus.FILLED
    with pytest.raises(PolicyLimitExceeded):
        service.execute_order(agent_id="agg", quote_id=_spot_quote(service, "agg", "800"),
                              idempotency_key="agg-2")


def test_approval_flow(service: NumeraService) -> None:
    service.set_policy(agent_id="a3", approval_threshold=_usd("500"))
    parked = service.execute_order(agent_id="a3", quote_id=_spot_quote(service, "a3", "800"),
                                   idempotency_key="a3-1")
    assert parked.order.status is OrderStatus.APPROVAL_REQUIRED
    assert parked.fill is None
    approved = service.approve_order(order_id=parked.order.id, approver="treasury")
    assert approved.order.status is OrderStatus.FILLED
    assert approved.fill is not None and approved.attribution is not None


def test_pair_not_allowed(service: NumeraService) -> None:
    service.set_policy(agent_id="a4", allowed_pairs=["EUR/USD"])
    with pytest.raises(PairNotAllowed):
        service.execute_order(agent_id="a4", quote_id=_spot_quote(service, "a4", "100"),
                              idempotency_key="a4-1")


def test_instrument_not_allowed(service: NumeraService) -> None:
    service.set_policy(agent_id="a5", allowed_instruments=["CONVERT"])
    decl = service.declare_exposure(
        agent_id="a5", principal_id="a5", given=Money.from_major("4200", "EUR"),
        target_currency="USD", direction=Direction.OWE, timing=Timing.FORWARD,
        value_date=date(2026, 9, 15),
    )
    qid = service.request_quote(exposure_id=decl.exposure.id).id
    with pytest.raises(InstrumentNotAllowed):
        service.execute_order(agent_id="a5", quote_id=qid, idempotency_key="a5-1")


def test_positions_track_net_exposure(service: NumeraService) -> None:
    service.execute_order(agent_id="pos", quote_id=_spot_quote(service, "pos", "1000"),
                          idempotency_key="pos-1")
    view = service.get_positions("pos")
    nets = {line.currency: line.net.amount_minor for line in view.positions}
    assert nets["USD"] == -100000  # paid 1,000.00 USD
    assert nets["INR"] > 0  # received INR
    assert view.aggregate_net_exposure.currency == "USD"
    assert view.aggregate_net_exposure.amount_minor > 0


# ---- HTTP surface -------------------------------------------------------------------------
def test_http_policy_rejection_and_positions(client: TestClient) -> None:
    h = {"X-Agent-Id": "httpcap"}
    assert client.put("/policies/httpcap", json={
        "max_single_ticket": {"amount_minor": 100000, "currency": "USD"},
    }).status_code == 200

    decl = client.post("/exposures", headers=h, json={
        "given": {"amount_minor": 180000, "currency": "USD"},
        "target_currency": "INR", "direction": "HAVE", "timing": "SPOT",
    })
    quote = client.post("/quotes", json={"exposure_id": decl.json()["exposure"]["id"]}).json()
    rejected = client.post("/orders", headers={**h, "Idempotency-Key": "h1"},
                           json={"quote_id": quote["id"]})
    assert rejected.status_code == 402
    assert rejected.json()["error"]["code"] == "POLICY_LIMIT_EXCEEDED"

    assert client.get("/positions", headers=h).json()["positions"] == []  # nothing executed
