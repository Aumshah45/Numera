"""Composition root: wires concrete adapters into the ports the use-cases depend on.

This is the *only* place that knows which concrete implementations are used. Tests build a
container with deterministic adapters (FixedClock, SimRateFeed, in-memory repos); production uses
the real clock and (optionally) the real rate feed — selected by :class:`Settings`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..adapters.calendar import WeekendCalendar
from ..adapters.clock import SystemClock
from ..adapters.observability import Metrics
from ..adapters.persistence.sqlite_repos import build_sqlite_repositories
from ..adapters.rates import FlatRateCurve, RealRateFeed, SimRateFeed
from ..adapters.repositories import Repositories
from ..adapters.slippage import FixedSlippage, NoSlippage, SeededSlippage, SlippageModel
from ..adapters.venue import SimulatedVenue
from ..domain.rate import Bps
from ..domain.services import CostAttributor, DecisionEngine, FeeConfig, MarkToMarket, Pricer
from ..ports import Clock, ExecutionVenue, RateFeed
from .config import Settings


@dataclass(slots=True)
class Container:
    clock: Clock
    rate_feed: RateFeed
    venue: ExecutionVenue
    decision_engine: DecisionEngine
    pricer: Pricer
    cost_attributor: CostAttributor
    mark_to_market: MarkToMarket
    repos: Repositories
    metrics: Metrics = field(default_factory=Metrics)


def build_container(settings: Settings | None = None, *, clock: Clock | None = None) -> Container:
    settings = settings or Settings()
    the_clock: Clock = clock or SystemClock()

    calendar = WeekendCalendar()

    slippage: SlippageModel
    if settings.slippage_mode == "fixed":
        slippage = FixedSlippage(Bps.of(settings.slippage_bps))
    elif settings.slippage_mode == "seeded":
        slippage = SeededSlippage(
            settings.slippage_seed,
            Decimal(settings.slippage_max_adverse_bps),
            Decimal(settings.slippage_max_favorable_bps),
        )
    else:
        slippage = NoSlippage()

    venue = SimulatedVenue(
        calendar,
        spread_bps=Bps.of(settings.venue_spread_bps),
        provider_fee_bps=Bps.of(settings.venue_provider_fee_bps),
        spot_lag_days=settings.spot_lag_days,
        slippage=slippage,
    )

    rate_feed: RateFeed
    if settings.rate_feed == "real":
        rate_feed = RealRateFeed(
            the_clock,
            base_url=settings.frankfurter_base_url,
            ttl_seconds=settings.rate_ttl_seconds,
        )
    else:
        rate_feed = SimRateFeed()

    pricer = Pricer(
        FeeConfig(
            platform_fee_bps=Bps.of(settings.platform_fee_bps),
            quote_ttl_seconds=settings.quote_ttl_seconds,
        ),
        rate_curve=FlatRateCurve(),
    )

    repos = (
        build_sqlite_repositories(settings.database_url)
        if settings.persistence == "sqlite" else Repositories()
    )

    return Container(
        clock=the_clock,
        rate_feed=rate_feed,
        venue=venue,
        decision_engine=DecisionEngine(),
        pricer=pricer,
        cost_attributor=CostAttributor(),
        mark_to_market=MarkToMarket(),
        repos=repos,
    )
