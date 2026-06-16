"""Application use-cases: declare → quote → execute → read (TRD §8, ARCHITECTURE §4).

A thin orchestration layer over the pure domain. It owns persistence, idempotency, exposure
lifecycle transitions, and audit emission. Both inbound adapters (HTTP now, MCP in Phase 2) call
these same methods so the surfaces cannot diverge (FR-28).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import NoReturn

from ..adapters.observability import Metrics, logger
from ..domain.currency import CurrencyPair
from ..domain.errors import (
    InstrumentNotAllowed,
    InvalidExposure,
    InvalidState,
    InvalidValueDate,
    NotFound,
    PairNotAllowed,
    PolicyLimitExceeded,
    QuoteExpired,
)
from ..domain.models import (
    AuditEvent,
    CostAttribution,
    Decision,
    Direction,
    Exposure,
    ExposureStatus,
    Fill,
    LedgerEntry,
    Order,
    OrderStatus,
    Policy,
    Position,
    Quote,
    QuoteStatus,
    Timing,
    new_id,
)
from ..domain.money import Money
from ..domain.rate import Rate
from ..domain.services import (
    CostAttributor,
    DecisionEngine,
    MarkToMarket,
    PolicyEngine,
    PolicyOutcome,
    PolicyResult,
    Pricer,
)
from ..ports import (
    AuditRepository,
    Clock,
    DecisionRepository,
    ExecutionVenue,
    ExposureRepository,
    IdempotencyStore,
    LedgerRepository,
    OrderRepository,
    PolicyRepository,
    PositionRepository,
    QuoteRepository,
    RateFeed,
)
from .container import Container


@dataclass(frozen=True, slots=True)
class ExposureDecision:
    exposure: Exposure
    decision: Decision


@dataclass(frozen=True, slots=True)
class OrderView:
    order: Order
    fill: Fill | None
    attribution: CostAttribution | None


@dataclass(frozen=True, slots=True)
class MarkToMarketView:
    order_id: str
    as_of: datetime
    current_mid: Rate
    locked_amount: Money  # the locked target leg of the fill
    current_value: Money  # current target-value of the base notional at the current mid
    unrealized_pnl: Money


@dataclass(frozen=True, slots=True)
class PositionLine:
    currency: str
    net: Money
    value_in_reference: Money


@dataclass(frozen=True, slots=True)
class PositionsView:
    agent_id: str
    reference_currency: str
    positions: list[PositionLine]
    aggregate_net_exposure: Money  # Σ |non-reference positions| valued in reference ccy


@dataclass(frozen=True, slots=True)
class ReconciliationLine:
    currency: str
    debit: Money
    credit: Money
    balanced: bool


@dataclass(frozen=True, slots=True)
class ReconciliationView:
    order_id: str
    venue_state: str
    balanced: bool  # ledger postings balance per currency AND the venue confirms the fill
    lines: list[ReconciliationLine]


@dataclass(frozen=True, slots=True)
class ReportView:
    agent_id: str
    reference_currency: str
    realized_cost: Money  # Σ all-in cost of filled orders, valued in the reference currency
    outstanding_exposure: Money  # aggregate net FX exposure (reference currency)
    order_counts: dict[str, int]  # by status


class NumeraService:
    """Façade exposing the use-cases. Constructed from a :class:`Container` (composition root)."""

    def __init__(self, c: Container) -> None:
        self._clock: Clock = c.clock
        self._rate_feed: RateFeed = c.rate_feed
        self._venue: ExecutionVenue = c.venue
        self._decision_engine: DecisionEngine = c.decision_engine
        self._pricer: Pricer = c.pricer
        self._attributor: CostAttributor = c.cost_attributor
        self._mtm: MarkToMarket = c.mark_to_market
        self._exposures: ExposureRepository = c.repos.exposures
        self._decisions: DecisionRepository = c.repos.decisions
        self._quotes: QuoteRepository = c.repos.quotes
        self._orders: OrderRepository = c.repos.orders
        self._attributions = c.repos.attributions
        self._audit: AuditRepository = c.repos.audit
        self._idempotency: IdempotencyStore = c.repos.idempotency
        self._policies: PolicyRepository = c.repos.policies
        self._positions: PositionRepository = c.repos.positions
        self._ledger: LedgerRepository = c.repos.ledger
        self._metrics: Metrics = c.metrics
        self._policy_engine = PolicyEngine()  # pure + stateless

    # -- use-case 1: declare exposure (FR-1/2/3) ------------------------------------------
    def declare_exposure(
        self,
        *,
        agent_id: str,
        principal_id: str,
        given: Money,
        target_currency: str,
        direction: Direction,
        timing: Timing,
        value_date: date | None = None,
        correlation_id: str | None = None,
    ) -> ExposureDecision:
        now = self._clock.now()
        cid = correlation_id or new_id()

        if given.amount_minor <= 0:
            raise InvalidExposure(
                "Exposure amount must be positive", details={"amount_minor": given.amount_minor}
            )
        if target_currency == given.currency:
            raise InvalidExposure(
                "target_currency must differ from the given currency",
                details={"currency": target_currency},
            )
        if timing is Timing.FORWARD and (value_date is None or value_date <= now.date()):
            raise InvalidValueDate(
                "Forward exposures require a future value_date",
                details={"value_date": str(value_date)},
            )

        exposure = Exposure(
            agent_id=agent_id,
            principal_id=principal_id,
            given=given,
            target_currency=target_currency,
            direction=direction,
            timing=timing,
            value_date=value_date,
            created_at=now,
            status=ExposureStatus.DECLARED,
        )
        self._exposures.add(exposure)
        self._emit(agent_id, "exposure.declared", "exposure", exposure.id, cid,
                   {"given": str(given), "target": target_currency, "timing": str(timing)})

        decision = self._decision_engine.decide(exposure, venue_name=self._venue.name, now=now)
        self._decisions.add(decision)
        exposure = exposure.with_status(ExposureStatus.DECIDED)
        self._exposures.update(exposure)
        self._emit(agent_id, "exposure.decided", "exposure", exposure.id, cid,
                   {"instrument": str(decision.instrument), "pair": str(decision.pair)})

        return ExposureDecision(exposure=exposure, decision=decision)

    # -- use-case 2: request quote (FR-4/5) -----------------------------------------------
    def request_quote(self, *, exposure_id: str, correlation_id: str | None = None) -> Quote:
        now = self._clock.now()
        cid = correlation_id or new_id()
        started = time.perf_counter()

        exposure = self._exposures.get(exposure_id)
        decision = self._decisions.get_by_exposure(exposure_id)
        mid = self._rate_feed.get_mid(decision.pair)
        venue_quote = self._venue.quote(
            pair=decision.pair, from_amount=exposure.given, mid_rate=mid, now=now,
            instrument=decision.instrument, requested_value_date=exposure.value_date,
        )
        quote = self._pricer.build_quote(
            exposure=exposure,
            decision=decision,
            mid_rate=mid,
            spread_bps=venue_quote.spread_bps,
            provider_fee_bps=venue_quote.provider_fee_bps,
            value_date=venue_quote.value_date,
            spot_value_date=venue_quote.spot_value_date,
            now=now,
        )
        self._quotes.add(quote)
        self._exposures.update(exposure.with_status(ExposureStatus.QUOTED))
        self._metrics.incr("quotes_created")
        self._metrics.observe("quote_latency_ms", (time.perf_counter() - started) * 1000)
        self._emit(exposure.agent_id, "quote.created", "quote", quote.id, cid,
                   {"mid": str(mid), "all_in": str(quote.all_in_rate),
                    "to_amount": str(quote.to_amount), "expires_at": quote.expires_at.isoformat()})
        return quote

    # -- use-case 3: execute order (FR-7/11/12, FR-19/20/21) ------------------------------
    def execute_order(
        self, *, agent_id: str, quote_id: str, idempotency_key: str,
        correlation_id: str | None = None,
    ) -> OrderView:
        now = self._clock.now()
        cid = correlation_id or new_id()

        # Idempotency: a retried request returns the original result (FR-11).
        existing = self._idempotency.get(agent_id, idempotency_key)
        if existing is not None:
            self._metrics.incr("idempotency_hits")
            return self.get_order(existing)

        quote = self._quotes.get(quote_id)
        self._ensure_not_expired(quote, now)

        # Pre-trade policy check, enforced server-side (FR-21).
        policy = self._policy_for(agent_id)
        result = self._evaluate_policy(agent_id, quote, policy, approved=False)
        if result.outcome is PolicyOutcome.REJECT:
            self._metrics.incr("orders_rejected")
            self._emit(agent_id, "order.rejected", "quote", quote.id, cid,
                       {"code": result.code, "message": result.message, **(result.details or {})})
            self._raise_policy(result)

        order = Order(quote_id=quote.id, agent_id=agent_id, idempotency_key=idempotency_key,
                      created_at=now, updated_at=now, status=OrderStatus.CREATED)
        self._orders.add(order)
        self._idempotency.put(agent_id, idempotency_key, order.id)

        if result.outcome is PolicyOutcome.REQUIRES_APPROVAL:
            order.transition(OrderStatus.APPROVAL_REQUIRED, now)
            self._orders.update(order)
            self._metrics.incr("orders_approval_required")
            self._emit(agent_id, "order.approval_required", "order", order.id, cid,
                       {"message": result.message, **(result.details or {})})
            return OrderView(order=order, fill=None, attribution=None)

        return self._execute_filled(order, quote, now, cid)

    def _execute_filled(
        self, order: Order, quote: Quote, now: datetime, cid: str,
        *, extra_payload: dict[str, object] | None = None,
    ) -> OrderView:
        """Run the execution + persistence for an order cleared to trade (FR-7/12/16/25)."""
        started = time.perf_counter()
        order.transition(OrderStatus.SUBMITTED, now)
        venue_fill = self._venue.execute(quote=quote, idempotency_key=order.idempotency_key)
        fill = Fill(
            order_id=order.id, executed_rate=venue_fill.executed_rate,
            from_amount=venue_fill.from_amount, to_amount=venue_fill.to_amount,
            value_date=venue_fill.value_date, venue=quote.venue, filled_at=now,
        )
        attribution = self._attributor.attribute(quote=quote, fill=fill)
        order.transition(OrderStatus.FILLED, now)
        order.attach_fill(fill)

        # Persist (single logical unit; a real DB wraps these in one transaction — TRD §7).
        self._orders.update(order)
        self._attributions.add(attribution)
        self._quotes.update(quote.with_status(QuoteStatus.ACCEPTED))
        self._exposures.update(
            self._exposures.get(quote.exposure_id).with_status(ExposureStatus.NEUTRALIZED)
        )
        self._apply_positions(order.agent_id, fill, now)
        self._post_ledger(order, fill, now)
        self._metrics.incr("orders_filled")
        self._metrics.observe("execute_latency_ms", (time.perf_counter() - started) * 1000)
        payload: dict[str, object] = {
            "executed_rate": str(fill.executed_rate), "to_amount": str(fill.to_amount),
            "all_in_cost": str(attribution.all_in.amount),
        }
        if extra_payload:
            payload.update(extra_payload)
        self._emit(order.agent_id, "order.filled", "order", order.id, cid, payload)
        return OrderView(order=order, fill=fill, attribution=attribution)

    # -- use-case 4: read order (FR-7/15) -------------------------------------------------
    def get_order(self, order_id: str) -> OrderView:
        order = self._orders.get(order_id)
        attribution = None
        if order.status is OrderStatus.FILLED:
            attribution = self._attributions.get_by_order(order_id)
        return OrderView(order=order, fill=order.fill, attribution=attribution)

    # -- use-case 5: read cost breakdown alone (FR-15) ------------------------------------
    def get_cost_breakdown(self, order_id: str) -> CostAttribution:
        self._orders.get(order_id)  # raises NotFound if the order does not exist
        return self._attributions.get_by_order(order_id)

    # -- use-case 6: mark-to-market an open position (FR-17) ------------------------------
    def get_mark_to_market(self, order_id: str) -> MarkToMarketView:
        order = self._orders.get(order_id)
        if order.fill is None:
            raise NotFound(
                f"Order {order_id} has no fill to mark", details={"order_id": order_id}
            )
        quote = self._quotes.get(order.quote_id)
        fill = order.fill
        base_ccy, target_ccy = quote.pair.base, quote.pair.quote
        base_leg = fill.from_amount if fill.from_amount.currency == base_ccy else fill.to_amount
        locked_target = (
            fill.from_amount if fill.from_amount.currency == target_ccy else fill.to_amount
        )

        current_mid = self._rate_feed.get_mid(CurrencyPair(base_ccy, target_ccy))
        result = self._mtm.value(
            base_notional=base_leg, locked_target=locked_target,
            current_mid=current_mid, direction=quote.direction,
        )
        return MarkToMarketView(
            order_id=order_id, as_of=self._clock.now(), current_mid=current_mid,
            locked_amount=locked_target, current_value=result.current_value,
            unrealized_pnl=result.unrealized_pnl,
        )

    # -- use-case 7: approve a parked order (FR-20) ---------------------------------------
    def approve_order(
        self, *, order_id: str, approver: str, correlation_id: str | None = None,
    ) -> OrderView:
        now = self._clock.now()
        cid = correlation_id or new_id()
        order = self._orders.get(order_id)
        if order.status is not OrderStatus.APPROVAL_REQUIRED:
            raise InvalidState(
                f"Order {order_id} is not awaiting approval",
                details={"order_id": order_id, "status": str(order.status)},
            )
        quote = self._quotes.get(order.quote_id)
        self._ensure_not_expired(quote, now)
        # Hard limits still apply after sign-off; only the approval threshold is waived.
        result = self._evaluate_policy(order.agent_id, quote, self._policy_for(order.agent_id),
                                       approved=True)
        if result.outcome is PolicyOutcome.REJECT:
            order.transition(OrderStatus.REJECTED, now)
            self._orders.update(order)
            self._emit(order.agent_id, "order.rejected", "order", order.id, cid,
                       {"code": result.code, "message": result.message, **(result.details or {})})
            self._raise_policy(result)
        self._emit(order.agent_id, "order.approved", "order", order.id, cid, {"approver": approver})
        return self._execute_filled(order, quote, now, cid, extra_payload={"approved_by": approver})

    # -- use-case 8: set per-agent policy/mandate (FR-19) ---------------------------------
    def set_policy(
        self, *, agent_id: str, reference_currency: str = "USD",
        max_single_ticket: Money | None = None,
        max_aggregate_net_exposure: Money | None = None,
        approval_threshold: Money | None = None,
        allowed_pairs: list[str] | None = None,
        allowed_instruments: list[str] | None = None,
        correlation_id: str | None = None,
    ) -> Policy:
        from ..domain.models import Instrument

        cid = correlation_id or new_id()
        policy = Policy(
            agent_id=agent_id, reference_currency=reference_currency,
            max_single_ticket=max_single_ticket,
            max_aggregate_net_exposure=max_aggregate_net_exposure,
            approval_threshold=approval_threshold,
            allowed_pairs=frozenset(allowed_pairs) if allowed_pairs is not None else None,
            allowed_instruments=(
                frozenset(Instrument(i) for i in allowed_instruments)
                if allowed_instruments is not None else None
            ),
        )
        self._policies.put(policy)
        self._emit(agent_id, "policy.set", "policy", agent_id, cid,
                   {"reference_currency": reference_currency})
        return policy

    # -- use-case 9: net-exposure positions (FR-16) ---------------------------------------
    def get_positions(self, agent_id: str) -> PositionsView:
        ref = self._policy_for(agent_id).reference_currency
        lines: list[PositionLine] = []
        aggregate = 0
        for p in self._positions.list(agent_id):
            valued = self._value_in_reference(p.as_money(), ref)
            lines.append(PositionLine(currency=p.currency, net=p.as_money(),
                                      value_in_reference=valued))
            if p.currency != ref:
                aggregate += abs(valued.amount_minor)
        return PositionsView(agent_id=agent_id, reference_currency=ref, positions=lines,
                             aggregate_net_exposure=Money(aggregate, ref))

    # -- use-case 10: query the audit trail (FR-23/24) ------------------------------------
    def get_audit(
        self, *, agent_id: str | None = None, subject_id: str | None = None,
        event_type: str | None = None, limit: int | None = None,
    ) -> list[AuditEvent]:
        return self._audit.list(agent_id=agent_id, subject_id=subject_id,
                                event_type=event_type, limit=limit)

    # -- use-case 11: reconcile an order vs the venue + ledger (FR-25) --------------------
    def reconcile(self, order_id: str) -> ReconciliationView:
        order = self._orders.get(order_id)
        venue_state = self._venue.status(order.id).state
        sums: dict[str, dict[str, int]] = {}
        for entry in self._ledger.list_for_ref(order.id):
            if entry.debit is not None:
                sums.setdefault(entry.debit.currency, {"debit": 0, "credit": 0})
                sums[entry.debit.currency]["debit"] += entry.debit.amount_minor
            if entry.credit is not None:
                sums.setdefault(entry.credit.currency, {"debit": 0, "credit": 0})
                sums[entry.credit.currency]["credit"] += entry.credit.amount_minor
        lines = [
            ReconciliationLine(currency=ccy, debit=Money(s["debit"], ccy),
                               credit=Money(s["credit"], ccy), balanced=s["debit"] == s["credit"])
            for ccy, s in sorted(sums.items())
        ]
        balanced = bool(lines) and all(line.balanced for line in lines) and venue_state == "FILLED"
        return ReconciliationView(order_id=order_id, venue_state=venue_state,
                                  balanced=balanced, lines=lines)

    # -- use-case 12: per-agent report (FR-18) --------------------------------------------
    def get_report(self, agent_id: str) -> ReportView:
        ref = self._policy_for(agent_id).reference_currency
        orders = self._orders.list(agent_id)
        counts: dict[str, int] = {}
        realized = 0
        for order in orders:
            counts[str(order.status)] = counts.get(str(order.status), 0) + 1
            if order.status is OrderStatus.FILLED:
                attr = self._attributions.get_by_order(order.id)
                realized += self._value_in_reference(attr.all_in.amount, ref).amount_minor
        outstanding = self.get_positions(agent_id).aggregate_net_exposure
        return ReportView(agent_id=agent_id, reference_currency=ref,
                          realized_cost=Money(realized, ref), outstanding_exposure=outstanding,
                          order_counts=counts)

    def metrics_snapshot(self) -> dict[str, object]:
        return self._metrics.snapshot()

    # -- helpers --------------------------------------------------------------------------
    def _post_ledger(self, order: Order, fill: Fill, now: datetime) -> None:
        """Post balanced double-entry rows for the fill (debits == credits per currency)."""
        agent = order.agent_id
        paid, received = fill.from_amount, fill.to_amount
        entries = [
            # Agent pays the `from` leg: credit the agent account, debit the venue clearing account.
            LedgerEntry(account=f"agent:{agent}:{paid.currency}", debit=None, credit=paid,
                        ref_type="order", ref_id=order.id, posted_at=now),
            LedgerEntry(account=f"venue:{paid.currency}", debit=paid, credit=None,
                        ref_type="order", ref_id=order.id, posted_at=now),
            # Agent receives the `to` leg: debit the agent account, credit the venue clearing.
            LedgerEntry(account=f"agent:{agent}:{received.currency}", debit=received, credit=None,
                        ref_type="order", ref_id=order.id, posted_at=now),
            LedgerEntry(account=f"venue:{received.currency}", debit=None, credit=received,
                        ref_type="order", ref_id=order.id, posted_at=now),
        ]
        for entry in entries:
            self._ledger.add(entry)

    def _policy_for(self, agent_id: str) -> Policy:
        return self._policies.get(agent_id) or Policy(agent_id=agent_id)

    def _ensure_not_expired(self, quote: Quote, now: datetime) -> None:
        if quote.is_expired(now):
            self._quotes.update(quote.with_status(QuoteStatus.EXPIRED))
            raise QuoteExpired(
                "Quote has expired; request a new one",
                details={"quote_id": quote.id, "expires_at": quote.expires_at.isoformat()},
            )

    def _evaluate_policy(
        self, agent_id: str, quote: Quote, policy: Policy, *, approved: bool,
    ) -> PolicyResult:
        ref = policy.reference_currency
        return self._policy_engine.evaluate(
            pair=quote.pair, instrument=quote.instrument,
            ticket_notional=self._ticket_notional(quote, ref),
            projected_aggregate=self._projected_aggregate(agent_id, quote, ref),
            policy=policy, approved=approved,
        )

    def _ticket_notional(self, quote: Quote, ref: str) -> Money:
        base_ccy = quote.pair.base
        base_leg = quote.from_amount if quote.from_amount.currency == base_ccy else quote.to_amount
        return self._value_in_reference(base_leg, ref)

    def _projected_aggregate(self, agent_id: str, quote: Quote, ref: str) -> Money:
        nets: dict[str, int] = {p.currency: p.net_minor for p in self._positions.list(agent_id)}
        f, t = quote.from_amount, quote.to_amount
        nets[f.currency] = nets.get(f.currency, 0) - f.amount_minor
        nets[t.currency] = nets.get(t.currency, 0) + t.amount_minor
        total = 0
        for ccy, net in nets.items():
            if ccy == ref or net == 0:
                continue
            total += abs(self._value_in_reference(Money(net, ccy), ref).amount_minor)
        return Money(total, ref)

    def _apply_positions(self, agent_id: str, fill: Fill, now: datetime) -> None:
        for money, sign in ((fill.from_amount, -1), (fill.to_amount, 1)):
            current = self._positions.get(agent_id, money.currency)
            base = current.net_minor if current else 0
            self._positions.upsert(Position(
                agent_id=agent_id, currency=money.currency,
                net_minor=base + sign * money.amount_minor, updated_at=now,
            ))

    def _value_in_reference(self, money: Money, ref: str) -> Money:
        if money.currency == ref:
            return money
        if money.amount_minor == 0:
            return Money(0, ref)
        mid = self._rate_feed.get_mid(CurrencyPair(money.currency, ref))
        return mid.convert(money, ref)

    def _raise_policy(self, result: PolicyResult) -> NoReturn:
        details = result.details or {}
        message = result.message or "Policy check failed"
        if result.code == "PAIR_NOT_ALLOWED":
            raise PairNotAllowed(message, details=details)
        if result.code == "INSTRUMENT_NOT_ALLOWED":
            raise InstrumentNotAllowed(message, details=details)
        raise PolicyLimitExceeded(message, details=details)

    def _emit(
        self, agent_id: str, event_type: str, subject_type: str, subject_id: str,
        correlation_id: str, payload: dict[str, object],
    ) -> None:
        self._audit.append(
            AuditEvent(
                agent_id=agent_id,
                event_type=event_type,
                subject_type=subject_type,
                subject_id=subject_id,
                payload=payload,
                occurred_at=self._clock.now(),
                correlation_id=correlation_id,
            )
        )
        logger.info(event_type, extra={"correlation_id": correlation_id, "agent_id": agent_id,
                                       "event_type": event_type, "subject_id": subject_id})
