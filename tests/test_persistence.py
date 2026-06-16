"""SQLite persistence behind the repository ports: round-trip, end-to-end, durability (NFR-7)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from numera.adapters.clock import FixedClock
from numera.application.config import Settings
from numera.application.container import Container, build_container
from numera.application.use_cases import NumeraService
from numera.domain.models import Direction, OrderStatus, Timing
from numera.domain.money import Money

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _ctx(tmp_path: Path) -> tuple[Container, NumeraService]:
    settings = Settings(persistence="sqlite", database_url=f"sqlite:///{tmp_path / 'numera.db'}")
    container = build_container(settings, clock=FixedClock(NOW))
    return container, NumeraService(container)


def test_forward_quote_round_trips_through_sqlite(tmp_path: Path) -> None:
    """A forward Quote (Rate/Bps/Decimal/date/enum fields) survives store + load unchanged."""
    container, service = _ctx(tmp_path)
    decl = service.declare_exposure(
        agent_id="a", principal_id="a", given=Money.from_major("4200", "EUR"),
        target_currency="USD", direction=Direction.OWE, timing=Timing.FORWARD,
        value_date=date(2026, 9, 15),
    )
    quote = service.request_quote(exposure_id=decl.exposure.id)
    assert container.repos.quotes.get(quote.id) == quote  # exact value-object round-trip


def test_sqlite_end_to_end_and_durable(tmp_path: Path) -> None:
    """Fill an order on one service; a fresh service on the same DB file sees it (durability)."""
    db = tmp_path / "numera.db"
    settings = Settings(persistence="sqlite", database_url=f"sqlite:///{db}")
    s1 = NumeraService(build_container(settings, clock=FixedClock(NOW)))

    decl = s1.declare_exposure(
        agent_id="a", principal_id="a", given=Money.from_major("1000", "USD"),
        target_currency="INR", direction=Direction.HAVE, timing=Timing.SPOT,
    )
    quote = s1.request_quote(exposure_id=decl.exposure.id)
    view = s1.execute_order(agent_id="a", quote_id=quote.id, idempotency_key="k")
    assert view.order.status is OrderStatus.FILLED
    assert view.attribution is not None and view.attribution.reconciles()

    # A brand-new service/engine on the same database file (a "restart").
    s2 = NumeraService(build_container(settings, clock=FixedClock(NOW)))
    again = s2.get_order(view.order.id)
    assert again.order.status is OrderStatus.FILLED
    assert again.attribution is not None and again.attribution.reconciles()
    assert s2.reconcile(view.order.id).balanced is True  # ledger persisted + balances
    assert {e.event_type for e in s2.get_audit(agent_id="a")} >= {"order.filled"}  # audit persisted
    # Idempotency key persisted: a retry returns the same order, no double-execute.
    assert s2.execute_order(agent_id="a", quote_id=quote.id, idempotency_key="k").order.id \
        == view.order.id
