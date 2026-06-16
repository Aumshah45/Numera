"""DecisionEngine normalisation (FR-3)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from numera.domain.models import Direction, Exposure, Instrument, Timing
from numera.domain.money import Money

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _exposure(timing: Timing, value_date: date | None = None) -> Exposure:
    return Exposure(
        agent_id="a", principal_id="p", given=Money.from_major("1800", "USD"),
        target_currency="INR", direction=Direction.HAVE, timing=timing,
        value_date=value_date, created_at=NOW,
    )


def test_spot_maps_to_convert() -> None:
    from numera.domain.services import DecisionEngine

    decision = DecisionEngine().decide(_exposure(Timing.SPOT), venue_name="sim", now=NOW)
    assert decision.instrument is Instrument.CONVERT
    assert (decision.pair.base, decision.pair.quote) == ("USD", "INR")


def test_forward_maps_to_hedge() -> None:
    from numera.domain.services import DecisionEngine

    decision = DecisionEngine().decide(
        _exposure(Timing.FORWARD, date(2026, 7, 15)), venue_name="sim", now=NOW
    )
    assert decision.instrument is Instrument.HEDGE
