# Numera — Design Decisions Log (ADRs)

A running, append-only log of architecturally significant decisions. The idea doc explicitly calls
for keeping this as the backbone of the write-up. Each record: **Context · Decision · Consequences ·
Status**. Append new records; supersede rather than delete.

Format: lightweight [ADR](https://adr.github.io/). Status ∈ `Accepted` · `Superseded` · `Proposed`.

---

## ADR-001 — Hexagonal architecture with an explicit venue seam
- **Status:** Accepted (2026-06-15)
- **Context:** The dominant constraint (PRD §9) is that real FX/derivatives execution is regulated.
  We must build the whole product now without touching real money, yet keep a credible path to a
  licensed partner later.
- **Decision:** Adopt **ports & adapters**. Make `ExecutionVenue` (`quote`/`execute`/`status`) the
  single boundary for execution. The domain core depends only on ports and is pure/I-O-free.
- **Consequences:** (+) The simulator→partner swap is a contained change validated by contract tests
  (SM-5). (+) Core is fully unit/property-testable. (−) Slightly more upfront interface design and
  dependency-injection wiring. (−) Some indirection for simple paths.

## ADR-002 — Simulate the execution venue first; defer real money entirely
- **Status:** Accepted (2026-06-15)
- **Context:** A solo builder cannot legally hold funds or execute regulated FX/derivatives without
  licensing/a partner. The defensible, demoable product is the abstraction/decisioning/attribution/
  policy layer.
- **Decision:** Ship a fully functional system against a **`SimulatedVenue`**; treat real venue
  integration, fund custody, KYC/AML, and live forwards as **explicitly deferred** (PRD §11).
- **Consequences:** (+) Entire product buildable solo, no legal exposure. (+) The deliberate
  regulatory-boundary design is itself a maturity signal. (−) Business go-live is gated on legal/
  partner work outside this repo's scope.

## ADR-003 — Python, with one shared core exposed via both HTTP and MCP
- **Status:** Accepted (2026-06-15)
- **Context:** User is fastest in Python. The folder was renamed from "MCP Wrapper" → agents are a
  first-class consumer, but a conventional API is also wanted. We must avoid logic divergence
  between surfaces.
- **Decision:** **Python 3.12+.** Build the use-cases once in an application layer; expose them via
  **FastAPI** (HTTP/JSON, OpenAPI) and the **official MCP SDK** as **thin adapters**. Shared Pydantic
  DTOs generate both schemas. Behavioral parity enforced by integration tests (SM-6).
- **Consequences:** (+) No duplicate business logic; both surfaces stay in sync. (+) Agent-native and
  developer-friendly. (−) Two adapters to maintain; must guard parity in CI.
- **Alternatives rejected:** TypeScript/Node (MCP SDK is TS-first) — set aside for Python fluency;
  MCP-only or HTTP-only — rejected to avoid lock-in.

## ADR-004 — Money as integer minor units + `Decimal`; banker's rounding
- **Status:** Accepted (2026-06-15)
- **Context:** Floating-point is unacceptable for currency; correctness here is the product's
  credibility (NFR-1).
- **Decision:** Store amounts as **integer minor units + ISO 4217 currency**; compute with
  **`decimal.Decimal`** under an explicit context; round to minor units with **`ROUND_HALF_EVEN`**.
  Centralize in a `Money` value object. Enforce invariants I1–I5 with **Hypothesis** property tests,
  including **attribution sums exactly to all-in cost (I2)**.
- **Consequences:** (+) Deterministic, auditable money math; no float drift. (+) Cross-currency
  arithmetic errors caught by type. (−) More ceremony than naive floats; explicit quantization points
  required.

## ADR-005 — Real mid-market rates, simulated spread/slippage/fills
- **Status:** Accepted (2026-06-15)
- **Context:** Cost attribution is only meaningful against a *real* reference mid; but execution must
  stay simulated (ADR-002).
- **Decision:** Pull **real mid** rates from a free public FX feed via a `RateFeed` port (cached,
  with fallback). Apply **spread, fees, and slippage in Numera/the SimulatedVenue**, not the feed.
- **Consequences:** (+) Attribution is honest and convincing. (+) Execution stays fully simulated.
  (−) External dependency + rate limits → mitigated by caching and a `SimRateFeed` for tests
  (OQ1).

## ADR-006 — Forward (hedge) pricing via covered interest-rate parity
- **Status:** Accepted (2026-06-15)
- **Context:** A future-dated hedge must be priced honestly, not hand-waved, to be credible.
- **Decision:** Price forwards with **CIP**: `F = S·(1 + r_quote·τ)/(1 + r_base·τ)`, with proper
  **day-count** (`ACT/360` default, `ACT/365` for some). Interest rates come from a `RateCurve`
  port; **v1 uses flat/simulated curves** (documented limitation, OQ2). Forwards remain a
  **simulated concept demonstration**, not a live regulated instrument.
- **Consequences:** (+) Structurally correct forward pricing and forward points. (+) Real curves can
  be added behind the port later. (−) v1 forward levels are only as realistic as the simple curve.

## ADR-007 — Append-only audit log + double-entry ledger
- **Status:** Accepted (2026-06-15)
- **Context:** Anything touching (even simulated) regulated money needs reconstructable history and
  internal consistency (FR-23–25).
- **Decision:** Maintain an **append-only `audit_events`** log (app role has no UPDATE/DELETE) plus a
  **double-entry `ledger_entries`** store whose postings must balance per operation. Provide
  **reconciliation** of internal state vs venue `status()`.
- **Consequences:** (+) Full auditability and a clean reconciliation story; strong engineering
  signal. (−) More write volume and discipline; every state change must emit balanced/append-only
  records.

## ADR-008 — PostgreSQL + SQLAlchemy, SQLite for dev/test
- **Status:** Accepted (2026-06-15)
- **Context:** Money data needs transactional integrity and exact decimals; tests need speed and
  zero setup.
- **Decision:** **PostgreSQL** (exact `NUMERIC`, transactions) via **SQLAlchemy 2.x** with
  **Alembic** migrations; **SQLite** for local/dev/test behind the same repository interfaces;
  in-memory repos for pure unit tests.
- **Consequences:** (+) Strong integrity in prod, fast feedback in tests, one repo abstraction. (−)
  Must avoid Postgres-only features that break SQLite parity, or guard them per-dialect.

## ADR-009 — Idempotency keys + single-transaction Unit-of-Work
- **Status:** Accepted (2026-06-15)
- **Context:** Money operations must be safe under retries and partial failures (NFR-2, NFR-5,
  FR-11).
- **Decision:** Require an **`Idempotency-Key`** on execute, unique per `(agent_id, key)`; a repeat
  returns the original result. Wrap order+fill+attribution+ledger+position+audit in **one DB
  transaction**; use **optimistic locking** on positions. A **transactional outbox** decouples a
  future real venue call from the commit.
- **Consequences:** (+) No double-execution, no inconsistent state. (−) Requires careful transaction
  boundaries and conflict handling.

---

### Template for new ADRs
```
## ADR-0NN — <short title>
- **Status:** Proposed | Accepted | Superseded (date)
- **Context:** <forces at play>
- **Decision:** <what we chose>
- **Consequences:** <(+) benefits / (−) costs / alternatives rejected>
```
