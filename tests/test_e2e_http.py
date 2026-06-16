"""HTTP end-to-end: declare -> quote -> execute -> read, plus idempotency and expiry."""

from __future__ import annotations

from fastapi.testclient import TestClient

from numera.adapters.clock import FixedClock

SPOT_EXPOSURE = {
    "given": {"amount_minor": 180000, "currency": "USD"},
    "target_currency": "INR",
    "direction": "HAVE",
    "timing": "SPOT",
}


def _declare_quote(client: TestClient) -> str:
    decl = client.post("/exposures", json=SPOT_EXPOSURE)
    assert decl.status_code == 201, decl.text
    assert decl.json()["decision"]["instrument"] == "CONVERT"
    exposure_id = decl.json()["exposure"]["id"]

    quoted = client.post("/quotes", json={"exposure_id": exposure_id})
    assert quoted.status_code == 201, quoted.text
    return quoted.json()["id"]


def test_spot_flow_end_to_end(client: TestClient) -> None:
    quote_id = _declare_quote(client)
    resp = client.post("/orders", json={"quote_id": quote_id},
                       headers={"Idempotency-Key": "k-1"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "FILLED"

    ca = body["cost_attribution"]
    assert ca is not None
    components = ["spread", "provider_fee", "platform_fee", "slippage", "rounding_residual"]
    total = sum(ca[c]["amount"]["amount_minor"] for c in components)
    assert total == ca["all_in"]["amount"]["amount_minor"]  # invariant I2 over the wire
    assert ca["slippage"]["amount"]["amount_minor"] == 0
    assert ca["all_in"]["amount"]["amount_minor"] > 0

    # GET returns the same filled order with attribution.
    fetched = client.get(f"/orders/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "FILLED"
    assert fetched.json()["cost_attribution"]["all_in"] == ca["all_in"]


def test_idempotent_retry_returns_same_order(client: TestClient) -> None:
    quote_id = _declare_quote(client)
    first = client.post("/orders", json={"quote_id": quote_id},
                        headers={"Idempotency-Key": "same-key"})
    second = client.post("/orders", json={"quote_id": quote_id},
                         headers={"Idempotency-Key": "same-key"})
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_expired_quote_is_rejected(client: TestClient, clock: FixedClock) -> None:
    quote_id = _declare_quote(client)
    clock.advance(200)  # quote TTL is 120s
    resp = client.post("/orders", json={"quote_id": quote_id},
                       headers={"Idempotency-Key": "k-expired"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "QUOTE_EXPIRED"


def test_unknown_currency_rejected(client: TestClient) -> None:
    bad = dict(SPOT_EXPOSURE, given={"amount_minor": 1000, "currency": "XYZ"})
    resp = client.post("/exposures", json=bad)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNKNOWN_CURRENCY"
