"""In-memory repository adapters (implement the repository ports).

Phase 1 persistence. They live behind the same ``numera.ports`` interfaces a SQLAlchemy/SQLite
implementation will, so swapping in a real database (Phase 1+ per the plan) is a contained change
(NFR-7). The audit store is append-only (FR-23): it exposes no update/delete.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.errors import NotFound
from ..domain.models import (
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
from ..ports import (
    AttributionRepository,
    AuditRepository,
    DecisionRepository,
    ExposureRepository,
    IdempotencyStore,
    LedgerRepository,
    OrderRepository,
    PolicyRepository,
    PositionRepository,
    QuoteRepository,
)


class InMemoryExposureRepository:
    def __init__(self) -> None:
        self._items: dict[str, Exposure] = {}

    def add(self, exposure: Exposure) -> None:
        self._items[exposure.id] = exposure

    def get(self, exposure_id: str) -> Exposure:
        try:
            return self._items[exposure_id]
        except KeyError:
            raise NotFound(f"Exposure {exposure_id} not found",
                           details={"exposure_id": exposure_id}) from None

    def update(self, exposure: Exposure) -> None:
        self._items[exposure.id] = exposure


class InMemoryDecisionRepository:
    def __init__(self) -> None:
        self._by_exposure: dict[str, Decision] = {}

    def add(self, decision: Decision) -> None:
        self._by_exposure[decision.exposure_id] = decision

    def get_by_exposure(self, exposure_id: str) -> Decision:
        try:
            return self._by_exposure[exposure_id]
        except KeyError:
            raise NotFound(f"Decision for exposure {exposure_id} not found",
                           details={"exposure_id": exposure_id}) from None


class InMemoryQuoteRepository:
    def __init__(self) -> None:
        self._items: dict[str, Quote] = {}

    def add(self, quote: Quote) -> None:
        self._items[quote.id] = quote

    def get(self, quote_id: str) -> Quote:
        try:
            return self._items[quote_id]
        except KeyError:
            raise NotFound(f"Quote {quote_id} not found",
                           details={"quote_id": quote_id}) from None

    def update(self, quote: Quote) -> None:
        self._items[quote.id] = quote


class InMemoryOrderRepository:
    def __init__(self) -> None:
        self._items: dict[str, Order] = {}

    def add(self, order: Order) -> None:
        self._items[order.id] = order

    def get(self, order_id: str) -> Order:
        try:
            return self._items[order_id]
        except KeyError:
            raise NotFound(f"Order {order_id} not found",
                           details={"order_id": order_id}) from None

    def update(self, order: Order) -> None:
        self._items[order.id] = order

    def list(self, agent_id: str) -> list[Order]:
        return [o for o in self._items.values() if o.agent_id == agent_id]


class InMemoryAttributionRepository:
    def __init__(self) -> None:
        self._by_order: dict[str, CostAttribution] = {}

    def add(self, attribution: CostAttribution) -> None:
        self._by_order[attribution.order_id] = attribution

    def get_by_order(self, order_id: str) -> CostAttribution:
        try:
            return self._by_order[order_id]
        except KeyError:
            raise NotFound(f"Cost attribution for order {order_id} not found",
                           details={"order_id": order_id}) from None


class InMemoryAuditRepository:
    """Append-only audit log (FR-23/24): supports append and filtered query, never update/delete."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    def list(
        self, *, agent_id: str | None = None, subject_id: str | None = None,
        event_type: str | None = None, limit: int | None = None,
    ) -> list[AuditEvent]:
        out = [
            e for e in self._events
            if (agent_id is None or e.agent_id == agent_id)
            and (subject_id is None or e.subject_id == subject_id)
            and (event_type is None or e.event_type == event_type)
        ]
        return out[-limit:] if limit is not None else out


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._keys: dict[tuple[str, str], str] = {}

    def get(self, agent_id: str, key: str) -> str | None:
        return self._keys.get((agent_id, key))

    def put(self, agent_id: str, key: str, order_id: str) -> None:
        self._keys[(agent_id, key)] = order_id


class InMemoryPolicyRepository:
    def __init__(self) -> None:
        self._by_agent: dict[str, Policy] = {}

    def get(self, agent_id: str) -> Policy | None:
        return self._by_agent.get(agent_id)

    def put(self, policy: Policy) -> None:
        self._by_agent[policy.agent_id] = policy


class InMemoryPositionRepository:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], Position] = {}

    def get(self, agent_id: str, currency: str) -> Position | None:
        return self._by_key.get((agent_id, currency))

    def upsert(self, position: Position) -> None:
        self._by_key[(position.agent_id, position.currency)] = position

    def list(self, agent_id: str) -> list[Position]:
        return [p for (a, _), p in self._by_key.items() if a == agent_id]


class InMemoryLedgerRepository:
    """Append-only double-entry ledger (FR-25)."""

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []

    def add(self, entry: LedgerEntry) -> None:
        self._entries.append(entry)

    def list_for_ref(self, ref_id: str) -> list[LedgerEntry]:
        return [e for e in self._entries if e.ref_id == ref_id]


@dataclass(slots=True)
class Repositories:
    """A bundle of repository instances passed to use-cases (composition root wires this).

    Fields are typed as the port Protocols, so the same bundle holds either the in-memory defaults
    or the SQLite implementations (see ``persistence.sqlite_repos.build_sqlite_repositories``)."""

    exposures: ExposureRepository = field(default_factory=InMemoryExposureRepository)
    decisions: DecisionRepository = field(default_factory=InMemoryDecisionRepository)
    quotes: QuoteRepository = field(default_factory=InMemoryQuoteRepository)
    orders: OrderRepository = field(default_factory=InMemoryOrderRepository)
    attributions: AttributionRepository = field(default_factory=InMemoryAttributionRepository)
    audit: AuditRepository = field(default_factory=InMemoryAuditRepository)
    idempotency: IdempotencyStore = field(default_factory=InMemoryIdempotencyStore)
    policies: PolicyRepository = field(default_factory=InMemoryPolicyRepository)
    positions: PositionRepository = field(default_factory=InMemoryPositionRepository)
    ledger: LedgerRepository = field(default_factory=InMemoryLedgerRepository)
