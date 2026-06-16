"""A second, independent ``ExecutionVenue`` implementation (FR-30, SM-5).

``FixedRateVenue`` is deliberately *different* from :class:`SimulatedVenue`: tighter default
economics, no slippage, and a simple calendar-day settlement (no business-day calendar). Its only
contract with the core is the :class:`numera.ports.ExecutionVenue` Protocol — it exists to prove
that a different venue (e.g. a future licensed partner) drops in behind the seam with **no change
above the port**, validated by the shared venue contract test suite.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ..domain.currency import CurrencyPair
from ..domain.errors import InvalidValueDate
from ..domain.models import Direction, Instrument, Quote
from ..domain.money import Money
from ..domain.rate import Bps, Rate
from ..ports import VenueFill, VenueQuote, VenueStatus


class FixedRateVenue:
    name = "fixed"

    def __init__(
        self,
        *,
        spread_bps: Bps | None = None,
        provider_fee_bps: Bps | None = None,
        settle_days: int = 2,
    ) -> None:
        self._spread_bps = spread_bps or Bps.of("20")
        self._provider_fee_bps = provider_fee_bps or Bps.of("8")
        self._settle_days = settle_days

    def quote(
        self,
        *,
        pair: CurrencyPair,
        from_amount: Money,
        mid_rate: Rate,
        now: datetime,
        instrument: Instrument,
        requested_value_date: date | None = None,
    ) -> VenueQuote:
        spot_value_date = now.date() + timedelta(days=self._settle_days)
        if instrument is Instrument.HEDGE:
            if requested_value_date is None:
                raise InvalidValueDate("A forward hedge requires a value_date")
            value_date = requested_value_date
        else:
            value_date = spot_value_date
        return VenueQuote(spread_bps=self._spread_bps, provider_fee_bps=self._provider_fee_bps,
                          value_date=value_date, spot_value_date=spot_value_date)

    def execute(self, *, quote: Quote, idempotency_key: str) -> VenueFill:
        # Deterministic: fills exactly at the quoted all-in rate (no slippage).
        executed_rate = quote.all_in_rate
        base_ccy = quote.pair.base
        base_leg = quote.from_amount if quote.from_amount.currency == base_ccy else quote.to_amount
        target_leg = executed_rate.convert(base_leg, quote.pair.quote)
        if quote.direction is Direction.HAVE:
            from_amount, to_amount = base_leg, target_leg
        else:
            from_amount, to_amount = target_leg, base_leg
        return VenueFill(executed_rate=executed_rate, from_amount=from_amount,
                         to_amount=to_amount, value_date=quote.value_date)

    def status(self, ref: str) -> VenueStatus:
        return VenueStatus(ref=ref, state="FILLED")
