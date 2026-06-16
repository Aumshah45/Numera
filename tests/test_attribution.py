"""Cost-attribution correctness (FR-13/14, invariant I2): golden + property-based."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from numera.domain.currency import CurrencyPair
from numera.domain.models import (
    CostAttribution,
    Decision,
    Direction,
    Exposure,
    Fill,
    Instrument,
    Timing,
)
from numera.domain.money import MINOR_UNIT_EXPONENT, Money
from numera.domain.rate import Bps, Rate
from numera.domain.services import CostAttributor, FeeConfig, Pricer

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _build(given_money: Money, target: str, mid: Rate, spread: Bps, provider: Bps,
           platform: Bps) -> tuple[CostAttribution, Money]:
    exposure = Exposure(
        agent_id="a", principal_id="p", given=given_money, target_currency=target,
        direction=Direction.HAVE, timing=Timing.SPOT, created_at=NOW,
    )
    decision = Decision(
        exposure_id=exposure.id, instrument=Instrument.CONVERT,
        pair=CurrencyPair(given_money.currency, target), venue="sim", rationale="", created_at=NOW,
    )
    pricer = Pricer(FeeConfig(platform_fee_bps=platform, quote_ttl_seconds=120))
    quote = pricer.build_quote(
        exposure=exposure, decision=decision, mid_rate=mid, spread_bps=spread,
        provider_fee_bps=provider, value_date=NOW.date(), now=NOW,
    )
    fill = Fill(
        order_id="o", executed_rate=quote.all_in_rate, from_amount=quote.from_amount,
        to_amount=quote.to_amount, value_date=quote.value_date, venue="sim", filled_at=NOW,
    )
    to_at_mid = mid.convert(quote.from_amount, target)
    return CostAttributor().attribute(quote=quote, fill=fill), to_at_mid


def test_golden_breakdown() -> None:
    """$1000 USD -> INR at mid 83.20 with 25/10/5 bps spread/provider/platform = 40 bps all-in."""
    attr, to_at_mid = _build(
        Money.from_major("1000", "USD"), "INR", Rate.of("83.20"),
        Bps.of("25"), Bps.of("10"), Bps.of("5"),
    )
    assert to_at_mid == Money.from_major("83200.00", "INR")
    assert attr.spread.amount == Money.from_major("208.00", "INR")
    assert attr.provider_fee.amount == Money.from_major("83.20", "INR")
    assert attr.platform_fee.amount == Money.from_major("41.60", "INR")
    assert attr.slippage.amount == Money.zero("INR")
    assert attr.rounding_residual.amount == Money.zero("INR")
    assert attr.all_in.amount == Money.from_major("332.80", "INR")
    assert attr.spread.bps.value == Decimal("25.00")
    assert attr.provider_fee.bps.value == Decimal("10.00")
    assert attr.platform_fee.bps.value == Decimal("5.00")
    assert attr.all_in.bps.value == Decimal("40.00")
    assert attr.reconciles()


currencies = sorted(MINOR_UNIT_EXPONENT)
mids = st.integers(min_value=50, max_value=20_000).map(lambda n: Rate(Decimal(n) / Decimal(100)))
majors = st.integers(min_value=1, max_value=100_000)
bps = st.integers(min_value=0, max_value=200).map(lambda n: Bps.of(n))
pairs = st.lists(st.sampled_from(currencies), min_size=2, max_size=2, unique=True)


@settings(max_examples=300)
@given(major=majors, pair=pairs, mid=mids, spread=bps, provider=bps, platform=bps)
def test_attribution_reconciles(major: int, pair: list[str], mid: Rate, spread: Bps,
                                provider: Bps, platform: Bps) -> None:
    """I2: components always sum exactly to the all-in cost; Phase 1 has zero slippage."""
    base, target = pair
    attr, _ = _build(Money.from_major(Decimal(major), base), target, mid, spread, provider,
                     platform)
    assert attr.reconciles()
    assert attr.slippage.amount.amount_minor == 0
    assert attr.spread.amount.amount_minor >= 0
    assert attr.provider_fee.amount.amount_minor >= 0
    assert attr.platform_fee.amount.amount_minor >= 0
    assert attr.all_in.amount.amount_minor >= 0
    # The residual should only ever absorb sub-unit rounding noise.
    assert abs(attr.rounding_residual.amount.amount_minor) <= 5
