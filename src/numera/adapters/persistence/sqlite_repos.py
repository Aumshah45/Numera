"""SQLite-backed repositories (SQLAlchemy Core) implementing the repository ports.

Each aggregate is stored as a JSON document keyed by its identifier(s), with a few extra indexed
columns for the queries the ports expose (order-by-agent, audit filters, ledger-by-ref). Upserts
use SQLite's ``INSERT OR REPLACE``. Returns a populated :class:`Repositories` bundle so the rest of
the system is unchanged (NFR-7).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine

from ...domain.errors import NotFound
from ...domain.models import (
    AuditEvent,
    CostAttribution,
    Decision,
    Exposure,
    LedgerEntry,
    Order,
    Policy,
    Position,
    Quote,
)
from ..repositories import Repositories
from .codec import decode, encode

_metadata = MetaData()


def _doc_table(name: str, *key_cols: Column[Any], extra: list[Column[Any]] | None = None) -> Table:
    return Table(name, _metadata, *key_cols, *(extra or []), Column("data", Text, nullable=False))


_exposures = _doc_table("exposures", Column("id", String, primary_key=True))
_decisions = _doc_table("decisions", Column("exposure_id", String, primary_key=True))
_quotes = _doc_table("quotes", Column("id", String, primary_key=True))
_orders = _doc_table("orders", Column("id", String, primary_key=True),
                     extra=[Column("agent_id", String, index=True)])
_attributions = _doc_table("attributions", Column("order_id", String, primary_key=True))
_policies = _doc_table("policies", Column("agent_id", String, primary_key=True))
_positions = _doc_table("positions", Column("agent_id", String, primary_key=True),
                        Column("currency", String, primary_key=True))
_audit = Table(
    "audit", _metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("agent_id", String, index=True), Column("subject_id", String, index=True),
    Column("event_type", String, index=True), Column("data", Text, nullable=False),
)
_ledger = Table(
    "ledger", _metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("ref_id", String, index=True), Column("data", Text, nullable=False),
)
_idempotency = Table(
    "idempotency", _metadata,
    Column("agent_id", String, primary_key=True), Column("key", String, primary_key=True),
    Column("order_id", String, nullable=False),
)


def _dump(obj: Any) -> str:
    return json.dumps(encode(obj))


class _Base:
    def __init__(self, engine: Engine) -> None:
        self._e = engine


class SqliteExposureRepository(_Base):
    def add(self, exposure: Exposure) -> None:
        with self._e.begin() as c:
            c.execute(_exposures.insert().prefix_with("OR REPLACE"),
                      {"id": exposure.id, "data": _dump(exposure)})

    update = add

    def get(self, exposure_id: str) -> Exposure:
        with self._e.connect() as c:
            row = c.execute(select(_exposures.c.data).where(_exposures.c.id == exposure_id)).first()
        if row is None:
            raise NotFound(f"Exposure {exposure_id} not found",
                           details={"exposure_id": exposure_id})
        return decode(Exposure, json.loads(row[0]))


class SqliteDecisionRepository(_Base):
    def add(self, decision: Decision) -> None:
        with self._e.begin() as c:
            c.execute(_decisions.insert().prefix_with("OR REPLACE"),
                      {"exposure_id": decision.exposure_id, "data": _dump(decision)})

    def get_by_exposure(self, exposure_id: str) -> Decision:
        with self._e.connect() as c:
            row = c.execute(
                select(_decisions.c.data).where(_decisions.c.exposure_id == exposure_id)
            ).first()
        if row is None:
            raise NotFound(f"Decision for exposure {exposure_id} not found",
                           details={"exposure_id": exposure_id})
        return decode(Decision, json.loads(row[0]))


class SqliteQuoteRepository(_Base):
    def add(self, quote: Quote) -> None:
        with self._e.begin() as c:
            c.execute(_quotes.insert().prefix_with("OR REPLACE"),
                      {"id": quote.id, "data": _dump(quote)})

    update = add

    def get(self, quote_id: str) -> Quote:
        with self._e.connect() as c:
            row = c.execute(select(_quotes.c.data).where(_quotes.c.id == quote_id)).first()
        if row is None:
            raise NotFound(f"Quote {quote_id} not found", details={"quote_id": quote_id})
        return decode(Quote, json.loads(row[0]))


class SqliteOrderRepository(_Base):
    def add(self, order: Order) -> None:
        with self._e.begin() as c:
            c.execute(_orders.insert().prefix_with("OR REPLACE"),
                      {"id": order.id, "agent_id": order.agent_id, "data": _dump(order)})

    update = add

    def get(self, order_id: str) -> Order:
        with self._e.connect() as c:
            row = c.execute(select(_orders.c.data).where(_orders.c.id == order_id)).first()
        if row is None:
            raise NotFound(f"Order {order_id} not found", details={"order_id": order_id})
        return decode(Order, json.loads(row[0]))

    def list(self, agent_id: str) -> list[Order]:
        with self._e.connect() as c:
            rows = c.execute(select(_orders.c.data).where(_orders.c.agent_id == agent_id)).all()
        return [decode(Order, json.loads(r[0])) for r in rows]


class SqliteAttributionRepository(_Base):
    def add(self, attribution: CostAttribution) -> None:
        with self._e.begin() as c:
            c.execute(_attributions.insert().prefix_with("OR REPLACE"),
                      {"order_id": attribution.order_id, "data": _dump(attribution)})

    def get_by_order(self, order_id: str) -> CostAttribution:
        with self._e.connect() as c:
            row = c.execute(
                select(_attributions.c.data).where(_attributions.c.order_id == order_id)
            ).first()
        if row is None:
            raise NotFound(f"Cost attribution for order {order_id} not found",
                           details={"order_id": order_id})
        return decode(CostAttribution, json.loads(row[0]))


class SqliteAuditRepository(_Base):
    def append(self, event: AuditEvent) -> None:
        with self._e.begin() as c:
            c.execute(_audit.insert(), {
                "agent_id": event.agent_id, "subject_id": event.subject_id,
                "event_type": event.event_type, "data": _dump(event),
            })

    def list(
        self, *, agent_id: str | None = None, subject_id: str | None = None,
        event_type: str | None = None, limit: int | None = None,
    ) -> list[AuditEvent]:
        stmt = select(_audit.c.data).order_by(_audit.c.seq)
        if agent_id is not None:
            stmt = stmt.where(_audit.c.agent_id == agent_id)
        if subject_id is not None:
            stmt = stmt.where(_audit.c.subject_id == subject_id)
        if event_type is not None:
            stmt = stmt.where(_audit.c.event_type == event_type)
        with self._e.connect() as c:
            rows = c.execute(stmt).all()
        events = [decode(AuditEvent, json.loads(r[0])) for r in rows]
        return events[-limit:] if limit is not None else events


class SqliteIdempotencyStore(_Base):
    def get(self, agent_id: str, key: str) -> str | None:
        with self._e.connect() as c:
            row = c.execute(
                select(_idempotency.c.order_id).where(
                    (_idempotency.c.agent_id == agent_id) & (_idempotency.c.key == key))
            ).first()
        return row[0] if row else None

    def put(self, agent_id: str, key: str, order_id: str) -> None:
        with self._e.begin() as c:
            c.execute(_idempotency.insert().prefix_with("OR REPLACE"),
                      {"agent_id": agent_id, "key": key, "order_id": order_id})


class SqlitePolicyRepository(_Base):
    def get(self, agent_id: str) -> Policy | None:
        with self._e.connect() as c:
            row = c.execute(
                select(_policies.c.data).where(_policies.c.agent_id == agent_id)
            ).first()
        return decode(Policy, json.loads(row[0])) if row else None

    def put(self, policy: Policy) -> None:
        with self._e.begin() as c:
            c.execute(_policies.insert().prefix_with("OR REPLACE"),
                      {"agent_id": policy.agent_id, "data": _dump(policy)})


class SqlitePositionRepository(_Base):
    def get(self, agent_id: str, currency: str) -> Position | None:
        with self._e.connect() as c:
            row = c.execute(select(_positions.c.data).where(
                (_positions.c.agent_id == agent_id) & (_positions.c.currency == currency))).first()
        return decode(Position, json.loads(row[0])) if row else None

    def upsert(self, position: Position) -> None:
        with self._e.begin() as c:
            c.execute(_positions.insert().prefix_with("OR REPLACE"),
                      {"agent_id": position.agent_id, "currency": position.currency,
                       "data": _dump(position)})

    def list(self, agent_id: str) -> list[Position]:
        with self._e.connect() as c:
            rows = c.execute(select(_positions.c.data).where(
                _positions.c.agent_id == agent_id)).all()
        return [decode(Position, json.loads(r[0])) for r in rows]


class SqliteLedgerRepository(_Base):
    def add(self, entry: LedgerEntry) -> None:
        with self._e.begin() as c:
            c.execute(_ledger.insert(), {"ref_id": entry.ref_id, "data": _dump(entry)})

    def list_for_ref(self, ref_id: str) -> list[LedgerEntry]:
        with self._e.connect() as c:
            rows = c.execute(select(_ledger.c.data).where(
                _ledger.c.ref_id == ref_id).order_by(_ledger.c.seq)).all()
        return [decode(LedgerEntry, json.loads(r[0])) for r in rows]


def build_sqlite_repositories(database_url: str = "sqlite:///numera.db") -> Repositories:
    """Create the schema (if needed) and return a :class:`Repositories` bundle of SQLite repos."""
    engine = create_engine(database_url, future=True)
    _metadata.create_all(engine)
    return Repositories(
        exposures=SqliteExposureRepository(engine),
        decisions=SqliteDecisionRepository(engine),
        quotes=SqliteQuoteRepository(engine),
        orders=SqliteOrderRepository(engine),
        attributions=SqliteAttributionRepository(engine),
        audit=SqliteAuditRepository(engine),
        idempotency=SqliteIdempotencyStore(engine),
        policies=SqlitePolicyRepository(engine),
        positions=SqlitePositionRepository(engine),
        ledger=SqliteLedgerRepository(engine),
    )
