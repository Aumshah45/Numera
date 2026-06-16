# Numera — Phased Build Plan

A detailed, phase-wise plan to build **Numera** as a single engineer. The defining constraint: real
FX/derivatives execution is regulated, so the entire system is built against a **simulated execution
venue**, architected so a licensed partner can be dropped in behind a clean seam with contained
change.

**Read first:** [`idea.md`](./idea.md) · **Specs:** [`PRD`](./docs/PRD.md) · [`TRD`](./docs/TRD.md) ·
[`ARCHITECTURE`](./docs/ARCHITECTURE.md) · [`DECISIONS`](./docs/DECISIONS.md) · [`GLOSSARY`](./docs/GLOSSARY.md)

> Requirement IDs (FR-/NFR-) and invariants (I1–I5) reference the PRD/TRD. Each phase lists a
> **Goal**, **Deliverables**, **Exit criteria**, and a **Test gate** (what must be green to proceed).

## Implementation status
- ✅ **Phase 0 & Phase 1 — implemented and verified.** Value objects + ISO 4217 table, ports,
  domain services (decision/pricer/attributor), `SimulatedVenue`, real (`RealRateFeed` via
  Frankfurter) + simulated rate feeds, in-memory repositories, application use-cases, and the
  FastAPI HTTP adapter. End-to-end spot conversion returns a fully-attributed fill.
- ✅ **Phase 2 — implemented and verified.** Quote/execute split with server-side TTL expiry;
  **seeded slippage model** attributed as its own line; idempotent execution; standalone
  `GET /orders/{id}/cost`; and an **MCP server** ([`adapters/mcp_server.py`](./src/numera/adapters/mcp_server.py))
  exposing the same use-cases over shared DTOs ([`adapters/dto.py`](./src/numera/adapters/dto.py)) —
  HTTP↔MCP parity is tested (SM-6).
- ✅ **Phase 3 — implemented and verified.** Future-dated **forward hedges priced via covered
  interest-rate parity** ([`domain/services.py`](./src/numera/domain/services.py)) with per-currency
  **day-count** (ACT/360 default, ACT/365 e.g. GBP/INR — [`domain/daycount.py`](./src/numera/domain/daycount.py)),
  forward points, and business-day value dates. Both **HAVE** and **OWE** directions are modelled
  (the OWE obligation leg is fixed; the cost leg is computed), with the cost attributor and slippage
  direction-aware while still reconciling (I2). **Mark-to-market** reports direction-aware
  unrealized P&L.
- ✅ **Phase 4 — implemented and verified.** Per-agent **mandates** (`PUT /policies/{agent_id}`,
  `set_policy` tool): single-ticket + aggregate net-exposure caps (in a reference currency),
  allowed pairs/instruments, and an approval threshold. A pure **`PolicyEngine`** runs a
  **server-side pre-trade check** (`ALLOW`/`REJECT`/`REQUIRES_APPROVAL`); over-limit attempts are
  cleanly rejected and audited, over-threshold orders park in `APPROVAL_REQUIRED` until
  `POST /orders/{id}/approve` (`approve_order` tool, hard caps still enforced). **Net-exposure
  positions** are tracked per agent×currency and valued in the reference currency
  (`GET /positions`, `get_position` tool). Checks green: `pytest` (45 tests incl. pure-engine
  cases, over-limit rejection, **sequential aggregate enforcement (SM-3)**, approval flow,
  allow-lists, positions, HTTP), `mypy`, `ruff`.
- ✅ **Phase 5 — implemented and verified.** A **queryable append-only audit trail**
  (`GET /audit`, `get_audit`) from which any order is reconstructable (SM-4); a **double-entry
  ledger** posted per fill + **reconciliation** vs the venue (`GET /orders/{id}/reconcile`,
  `reconcile_order`) that balances debits==credits per currency (FR-25); per-agent **reporting**
  (`GET /report`, `get_report` — realized cost, outstanding exposure, order counts, FR-18);
  **observability** (metrics + structured JSON logging with correlation IDs, `GET /metrics`,
  NFR-9); and a **venue contract test suite** ([`tests/test_venue_contract.py`](./tests/test_venue_contract.py))
  run against **two** independent `ExecutionVenue` impls (`SimulatedVenue`, `FixedRateVenue`) —
  proving the seam (SM-5). Checks green: `pytest` (60 tests), `mypy`, `ruff`.

**All planned phases (0–5) are complete.** The persistence follow-on is also complete: the same
repository ports can run in memory or against a SQLAlchemy/SQLite adapter for durable local state.
Remaining items are the explicitly-deferred regulated frontier: real venue/partner, fund custody,
KYC/AML, and live derivatives — see above.

> Notes: Phase 3 MTM revalues against the current **spot** mid (a documented v1 simplification — a
> full implementation re-forwards to the delivery date). Net-exposure caps/valuation use the
> current mid via the rate feed.

---

## Guiding approach

- **Simulator first, real money never (until licensed).** Every phase runs against the
  `SimulatedVenue`. No legal exposure. (ADR-002, PRD §9.)
- **Clean seam at the execution boundary.** `ExecutionVenue` is the only place execution happens;
  everything above it is ours to build freely. (ADR-001.)
- **Correctness & auditability from day one.** Even simulated, treat money with integer minor units +
  `Decimal`, banker's rounding, idempotency, and append-only audit. (ADR-004/007/009.)
- **Declarative interface.** The agent declares an *exposure*; the system decides the *action*. Never
  expose a raw order book.
- **One core, two surfaces.** Build use-cases once; expose via HTTP and MCP as thin adapters.
  (ADR-003.)

## ⚠️ Before any real-money step
This plan stops at a simulated venue **on purpose**. Going live requires resolving licensing (money
transmission, FX/brokerage, derivatives, KYC/AML) and almost certainly a licensed partner. **Get
qualified legal advice and a partner relationship in place before connecting anything to real
funds.** Nothing here is legal or financial advice. (PRD §9/§11.)

## Tech stack (locked — see TRD §1)
Python 3.12+ · FastAPI + Pydantic v2 · official MCP SDK · SQLAlchemy 2.x + Alembic (PostgreSQL prod,
SQLite dev/test) · `decimal.Decimal` + integer minor units · httpx · pytest + Hypothesis ·
ruff + mypy (strict) · uv/Poetry · pydantic-settings.

---

## Phase 0 — Model & seams *(short, foundational)*
**Goal:** Get the core abstractions and conventions right before building features.

**Deliverables**
- Repo scaffold: `pyproject.toml`, `src/numera/{domain,application,adapters,ports}`, `tests/`,
  tooling (ruff/mypy/pytest), CI skeleton.
- **Value objects:** `Money`, `CurrencyCode`, `CurrencyPair`, `Rate`, `Bps`; embedded **ISO 4217**
  minor-unit table. (TRD §2–3.)
- **Money/rounding conventions** implemented and documented (banker's rounding, quantization points).
- **Ports** defined as interfaces: `ExecutionVenue`, `RateFeed`, `RateCurve`, `BusinessCalendar`,
  `Clock`, and the repository set + `IdempotencyStore`. (TRD §10.)
- Domain object stubs (`Exposure`, `Decision`, `Quote`, `Order`, `Fill`, `CostAttribution`,
  `Position`, `Policy`, `AuditEvent`, `LedgerEntry`). (TRD §4.)
- Composition root / DI wiring placeholder; `DECISIONS.md` seeded (ADR-001…009 present).

**Exit criteria:** a written, code-backed domain model + a venue interface we're confident sits on
top of either a simulator or a real provider (NFR-7).

**Test gate:** Hypothesis tests for money invariants **I1, I3, I5** pass; `mypy --strict` clean.

---

## Phase 1 — Exposure intake + spot conversion (simulated)
**Goal:** End-to-end happy path for the simplest case, over HTTP. *First demo.*

**Deliverables**
- **DeclareExposure** use-case + `POST /exposures` (validation FR-2; decision FR-3).
- `DecisionEngine` for `SPOT → CONVERT`.
- **`SimulatedVenue`** (spot): quote with configurable spread + fill. (FR-8/29.)
- **`RealRateFeed`** (httpx) supplying the real **mid**, with cache + `SimRateFeed` for tests.
  (ADR-005, ARCH §7.)
- `Pricer` builds a `Quote`; `CostAttributor` produces the itemized breakdown (FR-13).
- Persistence (SQLite) for exposures/quotes/orders/fills/attribution; **per-action audit event**
  (FR-23).
- `POST /orders` (execute) + `GET /orders/{id}` returning **fill + cost attribution**.

**Exit criteria (SM-1):** an agent calls the API with a spot exposure and gets back a structured,
fully-attributed simulated fill.

**Test gate:** I2 (attribution sums to all-in) property test green; one golden cost-attribution
test; HTTP integration test for declare→quote→execute.

---

## Phase 2 — Quote/execute split, attribution rigor, idempotency, MCP parity
**Goal:** Make the transparency layer excellent and the system safe to retry; reach both surfaces.

**Deliverables**
- Formal **quote lifecycle** with **TTL/expiry**; server-side staleness check → `QUOTE_EXPIRED`
  (FR-5/6).
- **`SlippageModel`** (seeded) — model quote→fill gap and attribute it as its own line (FR-12).
- Full itemized breakdown: mid, spread, provider fee, platform fee, slippage, rounding residual —
  reconciling exactly to all-in (FR-13/14, I2); `GET /orders/{id}/cost` (FR-15).
- **Idempotency keys** on execute; `UNIQUE(agent_id, key)`; single-transaction Unit-of-Work
  (FR-11, NFR-2/5, ADR-009).
- **MCP server** exposing `declare_exposure`, `get_quote`, `execute_hedge`, `get_order`,
  `get_cost_breakdown`, `get_position` — at parity with HTTP (FR-27/28).
- Structured **error model** end-to-end (TRD §8.3).

**Exit criteria:** every fill carries a trustworthy, reconciling cost breakdown; retried calls are
safe (no double-execution); identical use-cases succeed over HTTP **and** MCP (SM-6).

**Test gate:** idempotent-retry integration test; HTTP↔MCP parity test; I2/I4 property tests;
quote-expiry test.

---

## Phase 3 — Future-dated hedge (simulated)
**Goal:** Support "I owe X in N days," not just instant conversion.

**Deliverables**
- Extend exposure with **future `value_date`**; `DecisionEngine` `FORWARD → HEDGE` (FR-9/10).
- **Forward pricing via CIP** (`F = S·(1+r_quote·τ)/(1+r_base·τ)`), forward points, **day-count**
  (`ACT/360` default, `ACT/365` configured); `RateCurve` port with **flat/simulated** v1 curves
  (ADR-006, TRD §3.3).
- **Value-date logic:** `BusinessCalendar` (weekend-only v1), `T+2`/`T+1` spot, business-day
  adjustment (TRD §3.2).
- **Mark-to-market** of open hedges/positions at current mid → unrealized P&L (FR-17).
- Spot vs hedge clearly distinct in API + data model (FR-10).

**Exit criteria:** an agent declares a future obligation and receives a structured, fully-attributed
simulated **hedge**; positions reflect open hedges with MTM.

**Test gate:** CIP unit tests (known inputs → expected forward & points); value-date/day-count tests;
MTM test; hedge attribution golden test.

> Note: real forwards/options are regulated derivatives — this phase is a *simulated concept
> demonstration*, not a live instrument. (PRD §9.)

---

## Phase 4 — Policy, guardrails & authority
**Goal:** Make it safe for an autonomous agent to use.

**Deliverables**
- **`Policy`/Mandate** per agent: max single-ticket, max aggregate net exposure, allowed pairs/
  instruments, windowed caps, **approval threshold** (FR-19/20); `PUT /policies/{agent_id}`.
- **`PolicyEngine`** pre-trade check returning `Allow | Reject | RequiresApproval` (FR-21).
- **Net-exposure tracking** per agent×currency, updated transactionally; checked under concurrency
  (FR-16, NFR-2).
- **Approval flow:** over-threshold orders park in `APPROVAL_REQUIRED`; `POST /orders/{id}/approve`
  (FR-20).
- Every breach → **structured, recoverable error** + audit (FR-22).

**Exit criteria (SM-3):** demonstrably, no sequence of agent calls exceeds the mandate; 100% of
over-limit attempts are cleanly rejected and logged.

**Test gate:** adversarial policy tests (concurrent executes can't both pass an aggregate check);
approval-flow integration test; over-limit rejection test.

---

## Phase 5 — Audit, observability & the partner seam
**Goal:** Make it auditable and (in principle) ready for a real venue.

**Deliverables**
- Complete, **queryable audit trail** (`GET /audit`, filters) covering every exposure/quote/
  decision/policy-check/fill (FR-23/24).
- **Double-entry ledger** postings per operation + **reconciliation** check vs venue `status()`
  (FR-25, ADR-007).
- Per-agent **reporting:** realized cost, outstanding exposure, history (FR-18).
- **Observability:** structured logs + correlation IDs; metrics (quote latency, slippage
  distribution, policy-rejection/idempotency-hit rates) (NFR-9).
- **Harden the `ExecutionVenue` contract** + a **contract-test suite**; document exactly what a
  licensed partner must implement (FR-30, ARCH §11).
- **Package the MCP server** for agent consumption; demo script (PRD §13).

**Exit criteria (SM-4/SM-5):** any historical order is reconstructable from the audit trail alone; a
second `ExecutionVenue` implementation passes the **same** contract suite with **no core changes**.

**Test gate:** full contract suite green against the simulator; audit-reconstruction test;
reconciliation test; end-to-end demo script passes over both HTTP and MCP.

---

## Explicitly deferred (the regulated frontier)
Out of scope for the solo build; each a business/legal milestone gated on counsel + a licensed
partner (PRD §11):
- Connecting to a **real** FX/payments/brokerage provider.
- **Holding/moving/custodying** real customer funds.
- Real **KYC/AML** onboarding.
- Any **live** forward/option execution (regulated derivatives).
- Going to market with real users.

---

## Requirement → phase traceability

| Requirement | P0 | P1 | P2 | P3 | P4 | P5 |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| FR-1/2/3 exposure intake + decision | ◐ | ● | | ● | | |
| FR-4/5/6 quoting + TTL | | ● | ● | | | |
| FR-7/8 spot execution + fill | | ● | | | | |
| FR-9/10 forward hedge + spot/hedge distinction | | | | ● | | |
| FR-11 idempotency | | | ● | | | |
| FR-12 slippage | | | ● | | | |
| FR-13/14/15 cost attribution | | ● | ● | ● | | |
| FR-16/17/18 positions, MTM, reporting | | | | ● | ● | ● |
| FR-19/20/21/22 policy & guardrails | | | | | ● | |
| FR-23/24/25 audit & reconciliation | | ◐ | | | ◐ | ● |
| FR-26/27/28 HTTP + MCP parity | | ● | ● | | | |
| FR-29/30 venue seam + contract tests | ◐ | ● | | | | ● |
| NFR-1 money correctness | ● | ● | ● | ● | | |
| NFR-2/5 idempotency/atomicity | | | ● | | ● | |
| NFR-3 auditability | | ◐ | | | | ● |
| NFR-6 security/authority | | ◐ | | | ● | |
| NFR-7 seam portability | ● | | | | | ● |
| NFR-8 testability/determinism | ● | ● | ● | ● | | |
| NFR-9 observability | | | | | | ● |
| NFR-10 docs/decision log | ● | ◐ | ◐ | ◐ | ◐ | ● |

● = primarily delivered/closed in this phase · ◐ = partially established.

---

## Practical first move
Start at **Phase 0 → Phase 1**: nail the value objects + ports, then get a single simulated **spot
conversion** returning a fully-attributed fill over HTTP. Resist thinking about real execution — the
entire defensible, demoable build lives above the venue seam.

## How to frame it (portfolio / narrative)
- **Problem:** "Agents transacting cross-border carry unmanaged FX risk and have no programmatic way
  to neutralize it."
- **Built:** "A declarative, agent-first FX/hedging API with first-class cost attribution and
  server-side risk guardrails, proven end-to-end against a simulated venue and architected to drop in
  a licensed execution partner — exposed over both HTTP and MCP."
- **Depth signals:** precise money math (minor units + `Decimal`, banker's rounding, property tests),
  idempotency + single-tx writes, quote/slippage handling, CIP forward pricing, the cost-attribution
  engine, append-only audit + double-entry ledger, and server-side policy enforcement.
- **Maturity signal:** identifying the regulatory boundary and deliberately architecting around it
  (simulator + clean seam) — documented in `DECISIONS.md`.
