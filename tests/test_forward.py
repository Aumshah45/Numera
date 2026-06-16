"""Forward hedging: CIP pricing, day-count, OWE legs, attribution, mark-to-market (FR-9/10/17)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from numera.adapters.rates import FlatRateCurve
from numera.domain.currency import CurrencyPair
from numera.domain.daycount import year_fraction
from numera.domain.models import (
    Decision,
    Direction,
    Exposure,
    Fill,
    Instrument,
    Quote,
    Timing,
)
from numera.domain.money import Money
from numera.domain.rate import Bps, Rate
from numera.domain.services import CostAttributor, FeeConfig, MarkToMarket, Pricer

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
CURVE = FlatRateCurve()
SPOT_VD = date(2026, 6, 17)
DELIVERY = date(2026, 9, 15)  # 90 days after spot value date


def _forward_quote(given: Money, target: str, direction: Direction, mid: Rate) -> Quote:
    exposure = Exposure(agent_id="a", principal_id="p", given=given, target_currency=target,
                        direction=direction, timing=Timing.FORWARD, value_date=DELIVERY,
                        created_at=NOW)
    decision = Decision(exposure_id=exposure.id, instrument=Instrument.HEDGE,
                        pair=CurrencyPair(given.currency, target), venue="sim", rationale="",
                        created_at=NOW)
    pricer = Pricer(FeeConfig(platform_fee_bps=Bps.of("5"), quote_ttl_seconds=120),
                    rate_curve=CURVE)
    return pricer.build_quote(exposure=exposure, decision=decision, mid_rate=mid,
                              spread_bps=Bps.of("25"), provider_fee_bps=Bps.of("10"),
                              value_date=DELIVERY, spot_value_date=SPOT_VD, now=NOW)


def _cip(spot: Rate, pair: CurrencyPair, tenor_days: int) -> Decimal:
    tau_b, tau_q = year_fraction(pair.base, tenor_days), year_fraction(pair.quote, tenor_days)
    r_b, r_q = CURVE.rate(pair.base, tenor_days), CURVE.rate(pair.quote, tenor_days)
    return spot.value * (Decimal(1) + r_q * tau_q) / (Decimal(1) + r_b * tau_b)


def test_daycount_basis() -> None:
    assert year_fraction("USD", 360) == Decimal(1)  # ACT/360
    assert year_fraction("GBP", 365) == Decimal(1)  # ACT/365
    assert year_fraction("INR", 365) == Decimal(1)  # ACT/365


def test_cip_matches_formula_and_points_positive() -> None:
    """EUR/USD: USD (quote) rate > EUR (base) rate ⇒ forward trades above spot."""
    given = Money.from_major("100000", "EUR")
    quote = _forward_quote(given, "USD", Direction.HAVE, Rate.of("1.0850"))
    pair = CurrencyPair("EUR", "USD")
    assert quote.tenor_days == 90
    assert quote.spot_rate == Rate.of("1.0850")
    assert quote.mid_rate == Rate(_cip(Rate.of("1.0850"), pair, 90))  # golden vs CIP formula
    assert quote.forward_points is not None and quote.forward_points > 0


def test_cip_points_negative_when_base_rate_higher() -> None:
    """INR base (higher rate) vs USD quote ⇒ forward trades below spot (negative points)."""
    given = Money.from_major("100000", "INR")
    quote = _forward_quote(given, "USD", Direction.HAVE, Rate.of("0.0120"))
    assert quote.forward_points is not None and quote.forward_points < 0


def test_owe_hedge_fixes_the_obligation_leg() -> None:
    """'I owe 4,200 EUR in 30 days, book in USD': EUR leg is fixed, USD is the computed cost."""
    given = Money.from_major("4200", "EUR")
    quote = _forward_quote(given, "USD", Direction.OWE, Rate.of("1.0850"))
    assert quote.instrument is Instrument.HEDGE
    assert quote.direction is Direction.OWE
    assert quote.to_amount == given  # the obligation received is fixed
    assert quote.from_amount.currency == "USD"  # the agent pays USD
    # OWE pays more than the fair forward value (cost worsens the rate against the agent).
    fair_usd = quote.mid_rate.convert(given, "USD")
    assert quote.from_amount.amount_minor > fair_usd.amount_minor


def _fill_at_all_in(quote: Quote) -> Fill:
    return Fill(order_id="o", executed_rate=quote.all_in_rate, from_amount=quote.from_amount,
                to_amount=quote.to_amount, value_date=quote.value_date, venue="sim", filled_at=NOW)


def test_hedge_attribution_reconciles_have_and_owe() -> None:
    for direction, given, target in [
        (Direction.HAVE, Money.from_major("100000", "EUR"), "USD"),
        (Direction.OWE, Money.from_major("4200", "EUR"), "USD"),
    ]:
        quote = _forward_quote(given, target, direction, Rate.of("1.0850"))
        attr = CostAttributor().attribute(quote=quote, fill=_fill_at_all_in(quote))
        assert attr.reconciles()
        assert attr.slippage.amount.amount_minor == 0
        # spread+provider+platform = 40 bps measured against the forward mid.
        assert Decimal("39.5") <= attr.all_in.bps.value <= Decimal("40.5")


def test_mark_to_market_direction_aware() -> None:
    mtm = MarkToMarket()
    # HAVE: locked to receive 84,000 INR for 1,000 USD; now only worth 83,200 → +800 gain.
    have = mtm.value(base_notional=Money.from_major("1000", "USD"),
                     locked_target=Money.from_major("84000", "INR"),
                     current_mid=Rate.of("83.20"), direction=Direction.HAVE)
    assert have.unrealized_pnl == Money.from_major("800", "INR")
    # OWE: locked to pay 4,600 USD for 4,200 EUR; now would cost 4,620 → +20 gain from hedging.
    owe = mtm.value(base_notional=Money.from_major("4200", "EUR"),
                    locked_target=Money.from_major("4600", "USD"),
                    current_mid=Rate.of("1.10"), direction=Direction.OWE)
    assert owe.unrealized_pnl == Money.from_major("20", "USD")


def test_http_forward_flow_and_mtm(client: TestClient) -> None:
    decl = client.post("/exposures", json={
        "given": {"amount_minor": 420000, "currency": "EUR"}, "target_currency": "USD",
        "direction": "OWE", "timing": "FORWARD", "value_date": "2026-09-15",
    })
    assert decl.status_code == 201, decl.text
    assert decl.json()["decision"]["instrument"] == "HEDGE"

    quote = client.post("/quotes", json={"exposure_id": decl.json()["exposure"]["id"]}).json()
    assert quote["instrument"] == "HEDGE"
    assert quote["forward_points"] is not None
    assert quote["spot_rate"] is not None
    assert quote["to_amount"] == {"amount_minor": 420000, "currency": "EUR"}  # obligation fixed

    order = client.post("/orders", json={"quote_id": quote["id"]},
                        headers={"Idempotency-Key": "fwd-1"}).json()
    assert order["status"] == "FILLED"

    mtm = client.get(f"/orders/{order['id']}/mtm")
    assert mtm.status_code == 200
    assert mtm.json()["unrealized_pnl"]["currency"] == "USD"
