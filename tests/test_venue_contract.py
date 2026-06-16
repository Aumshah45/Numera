"""Venue contract suite (FR-30, SM-5).

Every ``ExecutionVenue`` implementation must satisfy these invariants. Running the same suite
against two independent implementations (``SimulatedVenue`` and ``FixedRateVenue``) proves the
seam: a future licensed partner can drop in with no change above the port.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from numera.adapters.calendar import WeekendCalendar
from numera.adapters.rates import FlatRateCurve
from numera.adapters.venue import SimulatedVenue
from numera.adapters.venue_fixed import FixedRateVenue
from numera.domain.currency import CurrencyPair
from numera.domain.models import Decision, Direction, Exposure, Instrument, Quote, Timing
from numera.domain.money import Money
from numera.domain.rate import Bps, Rate
from numera.domain.services import FeeConfig, Pricer
from numera.ports import ExecutionVenue

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
PRICER = Pricer(FeeConfig(platform_fee_bps=Bps.of("5"), quote_ttl_seconds=120),
                rate_curve=FlatRateCurve())

VENUES: list[ExecutionVenue] = [
    SimulatedVenue(WeekendCalendar()),         # business-day calendar, slippage-capable
    FixedRateVenue(),                          # calendar-day settlement, no slippage
]


def _quote(venue: ExecutionVenue, *, direction: Direction, instrument: Instrument,
           value_date: date | None = None) -> Quote:
    given = Money.from_major("10000", "EUR")
    pair = CurrencyPair("EUR", "USD")
    mid = Rate.of("1.0850")
    vq = venue.quote(pair=pair, from_amount=given, mid_rate=mid, now=NOW,
                     instrument=instrument, requested_value_date=value_date)
    timing = Timing.SPOT if instrument is Instrument.CONVERT else Timing.FORWARD
    exposure = Exposure(agent_id="a", principal_id="p", given=given, target_currency="USD",
                        direction=direction, timing=timing, value_date=value_date, created_at=NOW)
    decision = Decision(exposure_id=exposure.id, instrument=instrument, pair=pair,
                        venue=venue.name, rationale="", created_at=NOW)
    return PRICER.build_quote(exposure=exposure, decision=decision, mid_rate=mid,
                              spread_bps=vq.spread_bps, provider_fee_bps=vq.provider_fee_bps,
                              value_date=vq.value_date, spot_value_date=vq.spot_value_date, now=NOW)


@pytest.mark.parametrize("venue", VENUES, ids=lambda v: v.name)
def test_quote_value_dates(venue: ExecutionVenue) -> None:
    spot_vq = venue.quote(pair=CurrencyPair("EUR", "USD"), from_amount=Money.from_major("1", "EUR"),
                          mid_rate=Rate.of("1.0850"), now=NOW, instrument=Instrument.CONVERT)
    assert spot_vq.value_date == spot_vq.spot_value_date  # spot settles on the spot date
    assert spot_vq.value_date >= NOW.date()
    assert spot_vq.spread_bps.value >= 0 and spot_vq.provider_fee_bps.value >= 0

    fwd_vq = venue.quote(pair=CurrencyPair("EUR", "USD"), from_amount=Money.from_major("1", "EUR"),
                         mid_rate=Rate.of("1.0850"), now=NOW, instrument=Instrument.HEDGE,
                         requested_value_date=date(2026, 9, 15))
    assert fwd_vq.value_date >= fwd_vq.spot_value_date  # delivery is on/after spot


@pytest.mark.parametrize("venue", VENUES, ids=lambda v: v.name)
@pytest.mark.parametrize("direction", [Direction.HAVE, Direction.OWE])
def test_execute_produces_consistent_fill(venue: ExecutionVenue, direction: Direction) -> None:
    quote = _quote(venue, direction=direction, instrument=Instrument.CONVERT)
    fill = venue.execute(quote=quote, idempotency_key="k")

    assert fill.executed_rate.value > 0
    # Orientation matches the quote: same currencies on each leg.
    assert fill.from_amount.currency == quote.from_amount.currency
    assert fill.to_amount.currency == quote.to_amount.currency
    # The base (given) notional is preserved; the computed leg is in the target currency.
    base = "EUR"
    base_leg = fill.from_amount if fill.from_amount.currency == base else fill.to_amount
    quoted_base = quote.from_amount if quote.from_amount.currency == base else quote.to_amount
    assert base_leg == quoted_base
    assert fill.value_date == quote.value_date


@pytest.mark.parametrize("venue", VENUES, ids=lambda v: v.name)
def test_execute_is_deterministic_per_key(venue: ExecutionVenue) -> None:
    quote = _quote(venue, direction=Direction.HAVE, instrument=Instrument.CONVERT)
    a = venue.execute(quote=quote, idempotency_key="same")
    b = venue.execute(quote=quote, idempotency_key="same")
    assert a == b


@pytest.mark.parametrize("venue", VENUES, ids=lambda v: v.name)
def test_status_confirms_fill(venue: ExecutionVenue) -> None:
    assert venue.status("any-ref").state == "FILLED"
