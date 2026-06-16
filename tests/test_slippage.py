"""Slippage modelling and its honest attribution (FR-12)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from numera.adapters.calendar import WeekendCalendar
from numera.adapters.slippage import FixedSlippage, NoSlippage, SeededSlippage
from numera.adapters.venue import SimulatedVenue
from numera.domain.currency import CurrencyPair
from numera.domain.models import Decision, Direction, Exposure, Fill, Instrument, Timing
from numera.domain.money import Money
from numera.domain.rate import Bps, Rate
from numera.domain.services import CostAttributor, FeeConfig, Pricer

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _make_quote():
    given = Money.from_major("1000", "USD")
    exposure = Exposure(agent_id="a", principal_id="p", given=given, target_currency="INR",
                        direction=Direction.HAVE, timing=Timing.SPOT, created_at=NOW)
    decision = Decision(exposure_id=exposure.id, instrument=Instrument.CONVERT,
                        pair=CurrencyPair("USD", "INR"), venue="sim", rationale="", created_at=NOW)
    pricer = Pricer(FeeConfig(platform_fee_bps=Bps.of("5"), quote_ttl_seconds=120))
    return pricer.build_quote(exposure=exposure, decision=decision, mid_rate=Rate.of("83.20"),
                              spread_bps=Bps.of("25"), provider_fee_bps=Bps.of("10"),
                              value_date=NOW.date(), now=NOW)


def _attribute(venue: SimulatedVenue, quote, key: str):
    vf = venue.execute(quote=quote, idempotency_key=key)
    fill = Fill(order_id="o", executed_rate=vf.executed_rate, from_amount=vf.from_amount,
                to_amount=vf.to_amount, value_date=vf.value_date, venue="sim", filled_at=NOW)
    return CostAttributor().attribute(quote=quote, fill=fill), vf


def test_no_slippage_fills_at_quote() -> None:
    quote = _make_quote()
    _, vf = _attribute(SimulatedVenue(WeekendCalendar()), quote, "k")
    assert vf.to_amount == quote.to_amount


def test_fixed_adverse_slippage_is_attributed() -> None:
    quote = _make_quote()
    venue = SimulatedVenue(WeekendCalendar(), slippage=FixedSlippage(Bps.of("5")))
    attr, vf = _attribute(venue, quote, "k")
    # Adverse slippage => the agent receives fewer target units than quoted.
    assert vf.to_amount.amount_minor < quote.to_amount.amount_minor
    assert attr.slippage.amount.amount_minor > 0
    assert Decimal("4") <= attr.slippage.bps.value <= Decimal("6")  # ~5 bps
    assert attr.reconciles()  # invariant I2 still holds with non-zero slippage


def test_seeded_slippage_is_deterministic_and_bounded() -> None:
    a = SeededSlippage(7, Decimal("8"), Decimal("3"))
    b = SeededSlippage(7, Decimal("8"), Decimal("3"))
    assert a.sample("order-x") == b.sample("order-x")  # reproducible given the key
    for key in ("o1", "o2", "o3", "o4"):
        s = a.sample(key)
        assert Decimal("-3") <= s.value <= Decimal("8")


def test_seeded_slippage_reconciles() -> None:
    quote = _make_quote()
    venue = SimulatedVenue(WeekendCalendar(),
                           slippage=SeededSlippage(1, Decimal("8"), Decimal("3")))
    attr, _ = _attribute(venue, quote, "order-42")
    assert attr.reconciles()
    assert isinstance(NoSlippage().sample("x"), Bps)
