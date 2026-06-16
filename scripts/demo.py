"""End-to-end demo: a spot conversion and a future-dated forward hedge, both fully attributed.

Runs fully in-process against the SimulatedVenue. Uses the simulated rate feed by default
(no network); set NUMERA_RATE_FEED=real to use live mid-market rates from Frankfurter, or
NUMERA_SLIPPAGE_MODE=seeded to see a non-zero slippage line.

    python scripts/demo.py
"""

from __future__ import annotations

from datetime import date, timedelta

from numera.application.container import build_container
from numera.application.use_cases import NumeraService
from numera.domain.errors import PolicyLimitExceeded
from numera.domain.models import Direction, OrderStatus, Timing
from numera.domain.money import Money


def _print_attribution(attr) -> None:  # type: ignore[no-untyped-def]
    print("Cost attribution (vs mid):")
    print(f"  spread          {attr.spread.amount}  ({attr.spread.bps})")
    print(f"  provider fee    {attr.provider_fee.amount}  ({attr.provider_fee.bps})")
    print(f"  platform fee    {attr.platform_fee.amount}  ({attr.platform_fee.bps})")
    print(f"  slippage        {attr.slippage.amount}  ({attr.slippage.bps})")
    print(f"  rounding resid. {attr.rounding_residual.amount}  ({attr.rounding_residual.bps})")
    print(f"  ALL-IN          {attr.all_in.amount}  ({attr.all_in.bps})")
    print(f"  reconciles? {attr.reconciles()}")


def main() -> None:
    service = NumeraService(build_container())

    # "I just got paid 1,800 USD but my books are in INR — convert it now."
    declared = service.declare_exposure(
        agent_id="agent-demo",
        principal_id="acme-corp",
        given=Money.from_major("1800", "USD"),
        target_currency="INR",
        direction=Direction.HAVE,
        timing=Timing.SPOT,
    )
    print(f"Exposure {declared.exposure.id} -> {declared.decision.instrument} "
          f"({declared.decision.pair}): {declared.decision.rationale}")

    quote = service.request_quote(exposure_id=declared.exposure.id)
    print(f"Quote {quote.id}: mid={quote.mid_rate} all_in={quote.all_in_rate} "
          f"-> receive {quote.to_amount} (value date {quote.value_date}, "
          f"expires {quote.expires_at.isoformat()})")

    result = service.execute_order(
        agent_id="agent-demo", quote_id=quote.id, idempotency_key="demo-key-1"
    )
    fill = result.fill
    attr = result.attribution
    assert fill is not None and attr is not None
    print(f"Filled order {result.order.id}: {fill.from_amount} -> {fill.to_amount} "
          f"@ {fill.executed_rate}")
    _print_attribution(attr)

    # Idempotent retry returns the same order, no double execution.
    retry = service.execute_order(
        agent_id="agent-demo", quote_id=quote.id, idempotency_key="demo-key-1"
    )
    print(f"Idempotent retry returned same order? {retry.order.id == result.order.id}")

    # ---- Forward hedge: "I owe 4,200 EUR in ~90 days; my book is in USD." ----------------
    print("\n=== Forward hedge (OWE) ===")
    declared_fwd = service.declare_exposure(
        agent_id="agent-demo",
        principal_id="acme-corp",
        given=Money.from_major("4200", "EUR"),
        target_currency="USD",
        direction=Direction.OWE,
        timing=Timing.FORWARD,
        value_date=date.today() + timedelta(days=90),
    )
    print(f"Exposure {declared_fwd.exposure.id} -> {declared_fwd.decision.instrument} "
          f"({declared_fwd.decision.pair}): {declared_fwd.decision.rationale}")

    fquote = service.request_quote(exposure_id=declared_fwd.exposure.id)
    print(f"Quote {fquote.id}: spot={fquote.spot_rate} forward(mid)={fquote.mid_rate} "
          f"points={fquote.forward_points} tenor={fquote.tenor_days}d "
          f"value_date={fquote.value_date}")
    print(f"  Lock: pay {fquote.from_amount} to receive {fquote.to_amount}")

    fresult = service.execute_order(
        agent_id="agent-demo", quote_id=fquote.id, idempotency_key="demo-fwd-1"
    )
    assert fresult.attribution is not None
    _print_attribution(fresult.attribution)

    mtm = service.get_mark_to_market(fresult.order.id)
    print(f"Mark-to-market @ {mtm.current_mid}: locked {mtm.locked_amount}, "
          f"now worth {mtm.current_value} -> unrealized P&L {mtm.unrealized_pnl}")

    # ---- Policy guardrails: a capped agent ----------------------------------------------
    print("\n=== Policy guardrails (agent 'capped') ===")
    capped = "capped"
    service.set_policy(agent_id=capped, reference_currency="USD",
                       max_single_ticket=Money.from_major("1000", "USD"),
                       approval_threshold=Money.from_major("500", "USD"))
    print("Mandate: max single ticket 1000 USD, approval threshold 500 USD")

    def _spot(amount_usd: str) -> str:  # returns quote id
        decl = service.declare_exposure(
            agent_id=capped, principal_id=capped, given=Money.from_major(amount_usd, "USD"),
            target_currency="INR", direction=Direction.HAVE, timing=Timing.SPOT,
        )
        return service.request_quote(exposure_id=decl.exposure.id).id

    # Over the single-ticket cap -> rejected server-side.
    try:
        service.execute_order(agent_id=capped, quote_id=_spot("1800"), idempotency_key="cap-1")
    except PolicyLimitExceeded as exc:
        print(f"1,800 USD ticket -> REJECTED [{exc.code}]: {exc.message}")

    # Over the approval threshold (but under the cap) -> parked for sign-off, then approved.
    parked = service.execute_order(agent_id=capped, quote_id=_spot("800"), idempotency_key="cap-2")
    print(f"800 USD ticket -> {parked.order.status}")
    if parked.order.status is OrderStatus.APPROVAL_REQUIRED:
        approved = service.approve_order(order_id=parked.order.id, approver="treasury")
        print(f"After sign-off -> {approved.order.status}")

    positions = service.get_positions(capped)
    print(f"Net exposure ({positions.reference_currency}): "
          f"aggregate {positions.aggregate_net_exposure}")
    for line in positions.positions:
        print(f"  {line.currency}: net {line.net}  (~{line.value_in_reference})")

    # ---- Audit, reconciliation & reporting ----------------------------------------------
    print("\n=== Audit, reconciliation & reporting ===")
    recon = service.reconcile(fresult.order.id)
    print(f"Reconcile forward order: venue={recon.venue_state}, balanced={recon.balanced}")
    for line in recon.lines:
        print(f"  {line.currency}: debit {line.debit} / credit {line.credit} "
              f"-> balanced {line.balanced}")

    report = service.get_report("agent-demo")
    print(f"Report (agent-demo): realized cost {report.realized_cost}, "
          f"outstanding {report.outstanding_exposure}, orders {report.order_counts}")

    events = service.get_audit(agent_id="agent-demo")
    print(f"Audit trail (agent-demo): {len(events)} events -> "
          f"{[e.event_type for e in events]}")

    print(f"Metrics: {service.metrics_snapshot()}")


if __name__ == "__main__":
    main()
