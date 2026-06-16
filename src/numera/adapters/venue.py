"""Simulated execution venue (implements :class:`numera.ports.ExecutionVenue`).

This is the only place "execution" happens, and it is fully simulated — **no real money moves**
(PRD §9, ADR-002). It owns the venue economics (spread + provider fee) and the settlement value
date (spot T+2, or a forward delivery date); Numera layers its own platform fee on top in the
:class:`~numera.domain.services.Pricer`.

The fill rate is the quoted all-in rate moved by the injected :class:`SlippageModel`, in the
direction that is adverse to the agent (FR-12). A licensed-partner adapter would implement this
same interface and pass the venue contract tests.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from ..domain.currency import CurrencyPair
from ..domain.errors import InvalidValueDate
from ..domain.models import Direction, Instrument, Quote
from ..domain.money import MONEY_CONTEXT, Money
from ..domain.rate import Bps, Rate
from ..ports import BusinessCalendar, VenueFill, VenueQuote, VenueStatus
from .slippage import NoSlippage, SlippageModel


class SimulatedVenue:
    name = "sim"

    def __init__(
        self,
        calendar: BusinessCalendar,
        *,
        spread_bps: Bps | None = None,
        provider_fee_bps: Bps | None = None,
        spot_lag_days: int = 2,
        slippage: SlippageModel | None = None,
    ) -> None:
        self._calendar = calendar
        self._spread_bps = spread_bps or Bps.of("25")
        self._provider_fee_bps = provider_fee_bps or Bps.of("10")
        self._spot_lag_days = spot_lag_days
        self._slippage: SlippageModel = slippage or NoSlippage()

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
        spot_value_date = self._calendar.add_business_days(now.date(), self._spot_lag_days)
        if instrument is Instrument.HEDGE:
            if requested_value_date is None:
                raise InvalidValueDate("A forward hedge requires a value_date")
            # Roll the requested delivery date to a business day (following convention).
            value_date = self._calendar.add_business_days(requested_value_date, 0)
        else:
            value_date = spot_value_date
        return VenueQuote(
            spread_bps=self._spread_bps,
            provider_fee_bps=self._provider_fee_bps,
            value_date=value_date,
            spot_value_date=spot_value_date,
        )

    def execute(self, *, quote: Quote, idempotency_key: str) -> VenueFill:
        # The market may have moved since the quote. Positive slippage is adverse: for a HAVE
        # conversion that means a lower rate (fewer target units); for an OWE hedge, a higher
        # rate (more target paid).
        slippage_fraction = self._slippage.sample(idempotency_key).as_fraction()
        if quote.direction is Direction.HAVE:
            fill_factor = MONEY_CONTEXT.subtract(Decimal(1), slippage_fraction)
        else:
            fill_factor = MONEY_CONTEXT.add(Decimal(1), slippage_fraction)
        executed_rate = quote.all_in_rate.scaled(fill_factor)

        base_ccy = quote.pair.base
        base_leg = quote.from_amount if quote.from_amount.currency == base_ccy else quote.to_amount
        target_leg = executed_rate.convert(base_leg, quote.pair.quote)

        if quote.direction is Direction.HAVE:
            from_amount, to_amount = base_leg, target_leg  # pay given, receive target
        else:
            from_amount, to_amount = target_leg, base_leg  # pay target, receive given

        return VenueFill(
            executed_rate=executed_rate,
            from_amount=from_amount,
            to_amount=to_amount,
            value_date=quote.value_date,
        )

    def status(self, ref: str) -> VenueStatus:
        return VenueStatus(ref=ref, state="FILLED")
