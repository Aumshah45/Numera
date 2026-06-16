"""Deterministic test fixtures (NFR-8): fixed clock, simulated rate feed, in-memory repos."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from numera.adapters.api import create_app
from numera.adapters.calendar import WeekendCalendar
from numera.adapters.clock import FixedClock
from numera.adapters.rates import FlatRateCurve, SimRateFeed
from numera.adapters.repositories import Repositories
from numera.adapters.venue import SimulatedVenue
from numera.application.container import Container
from numera.application.use_cases import NumeraService
from numera.domain.rate import Bps
from numera.domain.services import CostAttributor, DecisionEngine, FeeConfig, MarkToMarket, Pricer


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def container(clock: FixedClock) -> Container:
    calendar = WeekendCalendar()
    return Container(
        clock=clock,
        rate_feed=SimRateFeed(),
        venue=SimulatedVenue(calendar, spread_bps=Bps.of("25"), provider_fee_bps=Bps.of("10")),
        decision_engine=DecisionEngine(),
        pricer=Pricer(FeeConfig(platform_fee_bps=Bps.of("5"), quote_ttl_seconds=120),
                      rate_curve=FlatRateCurve()),
        cost_attributor=CostAttributor(),
        mark_to_market=MarkToMarket(),
        repos=Repositories(),
    )


@pytest.fixture
def service(container: Container) -> NumeraService:
    return NumeraService(container)


@pytest.fixture
def client(service: NumeraService) -> TestClient:
    return TestClient(create_app(service))
