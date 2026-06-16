"""Pure domain services: decisioning, pricing, cost attribution, mark-to-market (TRD §5).

These take their inputs explicitly (no I/O, no clock, no network) so they are fully
unit/property-testable. Two directional senses are modelled (TRD §4):

* **HAVE** — the agent holds ``given`` and converts it into ``target`` (receives target).
* **OWE** — the agent owes ``given`` and pays ``target`` to obtain it (the ``given`` leg is fixed).

Cost is always measured in the **target** currency against the reference mid (spot S for a
conversion, the forward F for a hedge), and the breakdown reconciles to the all-in cost (I2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from ..ports import RateCurve
from .currency import CurrencyPair
from .daycount import year_fraction
from .errors import AttributionImbalance, InstrumentNotSupported
from .models import (
    CostAttribution,
    CostComponent,
    Decision,
    Direction,
    Exposure,
    Fill,
    Instrument,
    Policy,
    Quote,
    QuoteStatus,
    Timing,
)
from .money import MONEY_CONTEXT, Money
from .rate import Bps, Rate


# --------------------------------------------------------------------------------------------
# Decision engine (FR-3)
# --------------------------------------------------------------------------------------------
class DecisionEngine:
    """Normalises an :class:`Exposure` into a :class:`Decision` (convert vs hedge)."""

    def decide(self, exposure: Exposure, *, venue_name: str, now: datetime) -> Decision:
        if exposure.timing is Timing.SPOT:
            instrument = Instrument.CONVERT
            rationale = (
                f"{exposure.direction} spot exposure in {exposure.given.currency} vs "
                f"{exposure.target_currency} book -> spot conversion"
            )
        else:
            instrument = Instrument.HEDGE
            rationale = (
                f"{exposure.direction} future-dated exposure ({exposure.value_date}) in "
                f"{exposure.given.currency} vs {exposure.target_currency} -> forward hedge"
            )
        # Pair orientation is given(base)/target(quote); rate is target units per 1 given unit.
        pair = CurrencyPair(exposure.given.currency, exposure.target_currency)
        return Decision(
            exposure_id=exposure.id,
            instrument=instrument,
            pair=pair,
            venue=venue_name,
            rationale=rationale,
            created_at=now,
        )


# --------------------------------------------------------------------------------------------
# Pricer / quote engine (FR-4, FR-9)
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class FeeConfig:
    """Numera's own economics, layered on top of the venue's spread/fee."""

    platform_fee_bps: Bps
    quote_ttl_seconds: int


class Pricer:
    """Builds a :class:`Quote`. Spot uses the mid directly; forwards use covered interest parity."""

    def __init__(self, fees: FeeConfig, rate_curve: RateCurve | None = None) -> None:
        self._fees = fees
        self._rate_curve = rate_curve

    def build_quote(
        self,
        *,
        exposure: Exposure,
        decision: Decision,
        mid_rate: Rate,  # spot mid S (target per given)
        spread_bps: Bps,
        provider_fee_bps: Bps,
        value_date: date,
        now: datetime,
        spot_value_date: date | None = None,
    ) -> Quote:
        platform_fee_bps = self._fees.platform_fee_bps
        cost_fraction = (spread_bps + provider_fee_bps + platform_fee_bps).as_fraction()
        pair = decision.pair

        spot_rate: Rate | None = None
        forward_points: Decimal | None = None
        tenor_days: int | None = None
        if decision.instrument is Instrument.HEDGE:
            if spot_value_date is None:
                raise InstrumentNotSupported(
                    "Forward pricing requires a spot value date",
                    details={"instrument": str(decision.instrument)},
                )
            tenor_days = max((value_date - spot_value_date).days, 0)
            reference = self._forward_rate(mid_rate, pair, tenor_days)
            spot_rate = mid_rate
            forward_points = MONEY_CONTEXT.subtract(reference.value, mid_rate.value)
        else:
            reference = mid_rate

        # Direction-aware legs. Cost always worsens the rate against the agent.
        if exposure.direction is Direction.HAVE:
            # Sell given(base), receive target(quote): a worse rate yields fewer target units.
            all_in_rate = reference.scaled(MONEY_CONTEXT.subtract(Decimal(1), cost_fraction))
            from_amount = exposure.given
            to_amount = all_in_rate.convert(exposure.given, exposure.target_currency)
        else:
            # OWE: pay target to obtain the fixed given(base) obligation; a worse rate costs more.
            all_in_rate = reference.scaled(MONEY_CONTEXT.add(Decimal(1), cost_fraction))
            to_amount = exposure.given
            from_amount = all_in_rate.convert(exposure.given, exposure.target_currency)

        return Quote(
            exposure_id=exposure.id,
            pair=pair,
            instrument=decision.instrument,
            direction=exposure.direction,
            mid_rate=reference,
            all_in_rate=all_in_rate,
            spread_bps=spread_bps,
            provider_fee_bps=provider_fee_bps,
            platform_fee_bps=platform_fee_bps,
            from_amount=from_amount,
            to_amount=to_amount,
            value_date=value_date,
            venue=decision.venue,
            created_at=now,
            expires_at=now + timedelta(seconds=self._fees.quote_ttl_seconds),
            spot_rate=spot_rate,
            forward_points=forward_points,
            tenor_days=tenor_days,
            status=QuoteStatus.QUOTED,
        )

    def _forward_rate(self, spot: Rate, pair: CurrencyPair, tenor_days: int) -> Rate:
        """Covered interest-rate parity: F = S · (1 + r_q·τ_q) / (1 + r_b·τ_b)."""
        if self._rate_curve is None:
            raise InstrumentNotSupported("Forward pricing requires a rate curve")
        tau_base = year_fraction(pair.base, tenor_days)
        tau_quote = year_fraction(pair.quote, tenor_days)
        r_base = self._rate_curve.rate(pair.base, tenor_days)
        r_quote = self._rate_curve.rate(pair.quote, tenor_days)
        numer = MONEY_CONTEXT.add(Decimal(1), MONEY_CONTEXT.multiply(r_quote, tau_quote))
        denom = MONEY_CONTEXT.add(Decimal(1), MONEY_CONTEXT.multiply(r_base, tau_base))
        return Rate(MONEY_CONTEXT.multiply(spot.value, MONEY_CONTEXT.divide(numer, denom)))


# --------------------------------------------------------------------------------------------
# Cost attributor (FR-13/14, invariant I2) — direction-aware
# --------------------------------------------------------------------------------------------
class CostAttributor:
    """Itemised cost breakdown in the target currency; reconciles to the all-in cost (I2)."""

    def attribute(self, *, quote: Quote, fill: Fill) -> CostAttribution:
        base_ccy, target_ccy = quote.pair.base, quote.pair.quote
        given_base = self._leg(quote.from_amount, quote.to_amount, base_ccy)
        quoted_target = self._leg(quote.from_amount, quote.to_amount, target_ccy)
        fill_target = self._leg(fill.from_amount, fill.to_amount, target_ccy)

        # Fair value of the base notional at the reference mid (always a positive notional).
        value_at_mid = quote.mid_rate.convert(given_base, target_ccy)
        sign = 1 if quote.direction is Direction.HAVE else -1

        spread_amt = self._component_amount(value_at_mid, quote.spread_bps)
        provider_amt = self._component_amount(value_at_mid, quote.provider_fee_bps)
        platform_amt = self._component_amount(value_at_mid, quote.platform_fee_bps)
        slippage_amt = self._signed(sign, quoted_target - fill_target)
        all_in_amt = self._signed(sign, value_at_mid - fill_target)
        # Residual absorbs sub-unit rounding so the breakdown reconciles exactly (I2).
        residual_amt = all_in_amt - (spread_amt + provider_amt + platform_amt + slippage_amt)

        attribution = CostAttribution(
            order_id=fill.order_id,
            mid_reference_rate=quote.mid_rate,
            spread=CostComponent(spread_amt, self._bps_of(spread_amt, value_at_mid)),
            provider_fee=CostComponent(provider_amt, self._bps_of(provider_amt, value_at_mid)),
            platform_fee=CostComponent(platform_amt, self._bps_of(platform_amt, value_at_mid)),
            slippage=CostComponent(slippage_amt, self._bps_of(slippage_amt, value_at_mid)),
            rounding_residual=CostComponent(residual_amt, self._bps_of(residual_amt, value_at_mid)),
            all_in=CostComponent(all_in_amt, self._bps_of(all_in_amt, value_at_mid)),
        )
        if not attribution.reconciles():
            raise AttributionImbalance(
                "Cost components do not reconcile to all-in cost",
                details={"order_id": fill.order_id},
            )
        return attribution

    @staticmethod
    def _leg(a: Money, b: Money, currency: str) -> Money:
        return a if a.currency == currency else b

    @staticmethod
    def _signed(sign: int, amount: Money) -> Money:
        return amount if sign == 1 else -amount

    @staticmethod
    def _component_amount(base: Money, bps: Bps) -> Money:
        major = MONEY_CONTEXT.multiply(base.as_decimal(), bps.as_fraction())
        return Money.from_major(major, base.currency)

    @staticmethod
    def _bps_of(amount: Money, base: Money) -> Bps:
        if base.amount_minor == 0:
            return Bps.of(0)
        fraction = MONEY_CONTEXT.divide(amount.as_decimal(), base.as_decimal())
        return Bps.from_fraction(fraction).quantized(2)


# --------------------------------------------------------------------------------------------
# Mark-to-market (FR-17) — direction-aware unrealized P&L vs the current mid
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class MarkToMarketResult:
    current_value: Money  # current target-value of the base notional at the current mid
    unrealized_pnl: Money  # signed; positive = the locked position is in the money


class MarkToMarket:
    """Revalue a filled position against a current reference rate.

    v1 simplification: the caller passes the current **spot** mid, so an open forward is compared
    to today's spot value rather than re-forwarded (and discounted) to its delivery date. The
    direction-aware sign is correct; the reference-rate refinement is a later enhancement.
    """

    def value(
        self, *, base_notional: Money, locked_target: Money, current_mid: Rate,
        direction: Direction,
    ) -> MarkToMarketResult:
        current_value = current_mid.convert(base_notional, locked_target.currency)
        if direction is Direction.HAVE:
            pnl = locked_target - current_value  # locked to receive more than converting now
        else:
            pnl = current_value - locked_target  # obtaining it now would cost more than locked
        return MarkToMarketResult(current_value=current_value, unrealized_pnl=pnl)


# --------------------------------------------------------------------------------------------
# Policy engine (FR-19/20/21) — pure pre-trade check
# --------------------------------------------------------------------------------------------
class PolicyOutcome(StrEnum):
    ALLOW = "ALLOW"
    REJECT = "REJECT"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"


@dataclass(frozen=True, slots=True)
class PolicyResult:
    outcome: PolicyOutcome
    code: str | None = None
    message: str | None = None
    details: dict[str, object] | None = None


class PolicyEngine:
    """Evaluates a mandate against a ticket. Pure: all amounts are pre-valued in the policy's
    reference currency by the caller, so this never performs I/O (FR-21)."""

    def evaluate(
        self,
        *,
        pair: CurrencyPair,
        instrument: Instrument,
        ticket_notional: Money,  # in policy.reference_currency
        projected_aggregate: Money,  # net FX exposure after this ticket, reference currency
        policy: Policy,
        approved: bool = False,
    ) -> PolicyResult:
        if policy.allowed_pairs is not None and str(pair) not in policy.allowed_pairs:
            return PolicyResult(PolicyOutcome.REJECT, "PAIR_NOT_ALLOWED",
                                f"Pair {pair} is not permitted by the mandate",
                                {"pair": str(pair)})
        if policy.allowed_instruments is not None and instrument not in policy.allowed_instruments:
            return PolicyResult(PolicyOutcome.REJECT, "INSTRUMENT_NOT_ALLOWED",
                                f"Instrument {instrument} is not permitted by the mandate",
                                {"instrument": str(instrument)})
        if (policy.max_single_ticket is not None
                and ticket_notional.amount_minor > policy.max_single_ticket.amount_minor):
            return PolicyResult(PolicyOutcome.REJECT, "POLICY_LIMIT_EXCEEDED",
                                "Ticket exceeds the maximum single-ticket limit",
                                {"limit": str(policy.max_single_ticket),
                                 "ticket": str(ticket_notional)})
        if (policy.max_aggregate_net_exposure is not None
                and projected_aggregate.amount_minor
                > policy.max_aggregate_net_exposure.amount_minor):
            return PolicyResult(PolicyOutcome.REJECT, "POLICY_LIMIT_EXCEEDED",
                                "Trade would exceed the maximum aggregate net exposure",
                                {"limit": str(policy.max_aggregate_net_exposure),
                                 "would_be": str(projected_aggregate)})
        if (not approved and policy.approval_threshold is not None
                and ticket_notional.amount_minor > policy.approval_threshold.amount_minor):
            return PolicyResult(PolicyOutcome.REQUIRES_APPROVAL, "APPROVAL_REQUIRED",
                                "Ticket exceeds the approval threshold; human sign-off required",
                                {"threshold": str(policy.approval_threshold),
                                 "ticket": str(ticket_notional)})
        return PolicyResult(PolicyOutcome.ALLOW)
