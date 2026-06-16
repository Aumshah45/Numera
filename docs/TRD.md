# Numera — Technical Requirements Document (TRD)

**Status:** Draft v1 · **Last updated:** 2026-06-15
**Related:** [`PRD`](./PRD.md) · [`ARCHITECTURE`](./ARCHITECTURE.md) · [`DECISIONS`](./DECISIONS.md) · [`GLOSSARY`](./GLOSSARY.md) · [`plan`](../plan.md)

> This document is the **single source of truth** for the domain model, money/FX conventions, API
> contracts, data model, and engineering rules. The PRD and Architecture reference it; they must not
> redefine these. FR-/NFR- IDs refer to the PRD.

---

## 1. Technology stack & rationale

| Concern | Choice | Why |
|---|---|---|
| Language / runtime | **Python 3.12+** | User's fluency; strong typing via type hints; mature finance/decimal ecosystem |
| HTTP API | **FastAPI + Pydantic v2** | Typed request/response, automatic OpenAPI, async; Pydantic enforces input validation (FR-2) |
| MCP server | **Official Python MCP SDK** (FastMCP-style) | Native agent tool surface (FR-27); mirrors HTTP use-cases |
| Persistence | **PostgreSQL** (prod) + **SQLite** (dev/test) via **SQLAlchemy 2.x** | Transactional integrity (NFR-5); exact `NUMERIC`; same repo interface for both |
| Migrations | **Alembic** | Versioned schema; auditable DB evolution |
| Money math | **`decimal.Decimal`** + integer minor units | No floats (NFR-1); arbitrary precision; explicit rounding |
| HTTP client (rate feed) | **httpx** (async) | Async, timeouts, retries for `RateFeed` adapter |
| Validation/schemas | **Pydantic v2** | Shared DTOs across HTTP + MCP; JSON Schema generation (FR-27) |
| Testing | **pytest + Hypothesis** | Property-based money-math invariants (NFR-1, FR-14); golden + contract tests |
| Lint/format/type | **ruff + mypy (strict)** | Quality gate; strict typing protects money code |
| Packaging/deps | **uv** (or Poetry) + `pyproject.toml` | Reproducible env |
| Config | **pydantic-settings** (env-driven) | 12-factor; per-environment venue/feed selection |

Async is used at the adapter edges (HTTP handlers, rate-feed I/O, DB). The **domain core is
synchronous and pure** (NFR-8) — no I/O, no clock, no randomness except injected ports.

---

## 2. Money & numeric conventions (NFR-1)

These rules are **non-negotiable** and enforced by value objects + tests.

### 2.1 Representation
- **Amounts are stored as integer minor units** (`amount_minor: int`) plus an ISO 4217 `currency`
  code. Example: `$12.34 USD` → `(1234, "USD")`; `¥1234 JPY` → `(1234, "JPY")` (JPY exponent 0).
- The per-currency **minor-unit exponent** comes from an embedded **ISO 4217 table**
  (`USD=2, EUR=2, GBP=2, INR=2, JPY=0, BHD=3, …`). No currency is supported unless it's in the table.
- **Rates** are stored/computed as `Decimal` with a fixed working precision (see §2.2), never as
  minor units (rates aren't currency amounts).

### 2.2 Computation
- All arithmetic uses Python `decimal.Decimal` under an **explicit `decimal.Context`** with
  sufficient precision (working precision **28 significant digits**; rates rounded to **10 decimal
  places** for storage/quoting).
- **Rounding mode: `ROUND_HALF_EVEN`** (banker's rounding) everywhere money is rounded to minor
  units. Documented and centralized.
- **Conversion of a money amount** through a rate: compute in `Decimal`, then **quantize to the
  target currency's minor unit** exactly once, at a defined point in the pipeline (after all-in rate
  is determined), recording any residual as part of attribution. Never round intermediate steps
  silently.

### 2.3 Invariants (property-tested via Hypothesis — FR-14, NFR-1)
- **I1 — Non-negative, well-formed:** every `Money` has a known currency and integer minor units.
- **I2 — Attribution sums to total:** `mid_cost + spread + provider_fee + platform_fee + slippage ==
  all_in_cost` to the minor unit (zero tolerance beyond defined rounding residual, which is itself a
  reported line).
- **I3 — No float anywhere in money paths:** enforced by type (`Money`/`Decimal` only) and a lint/CI
  check.
- **I4 — Inversion round-trip:** for a pair, converting `A→B→A` returns to within the documented
  rounding residual, and the residual is attributable.
- **I5 — Currency safety:** arithmetic between different currencies raises; only equal-currency
  `Money` may be added/subtracted.

---

## 3. FX conventions

### 3.1 Pairs & quoting
- A **`CurrencyPair`** is `BASE/QUOTE`; the rate is **QUOTE units per 1 BASE unit**
  (`EUR/USD = 1.0850` → 1 EUR = 1.0850 USD).
- The pair stores its **market-convention orientation**; requesting the reverse uses the **inverse
  rate** (`1/rate`) computed in `Decimal`. The value object owns orientation so quoting direction
  can't be inverted by accident (I4).

### 3.2 Value dates (settlement)
- **Spot:** `T+2` business days default; `T+1` for configured exceptions (e.g. USD/CAD). Trade date
  `T` is the system clock date (via `Clock` port).
- **Business-day adjustment:** "following" convention — if a computed value date is a non-business
  day, roll forward to the next business day.
- **v1 calendar:** weekend-only (Sat/Sun non-business). **Holiday calendars are a documented
  limitation (OQ4)** behind a `BusinessCalendar` port for later enrichment.

### 3.3 Forward pricing (hedge) — FR-9
- Forward rate via **Covered Interest-Rate Parity (CIP)**:

  ```
  F = S × (1 + r_quote · τ) / (1 + r_base · τ)
  forward_points = F − S
  ```

  where `S` = spot mid, `r_base`/`r_quote` = the two currencies' interest rates, `τ` = day-count
  year fraction from spot value date to the hedge value date.
- **Day-count:** `ACT/360` default; `ACT/365` for configured currencies (e.g. GBP). `τ = days / basis`.
- **Interest rates:** sourced from a **`RateCurve` port**; **v1 uses flat/simulated per-currency
  rates** (documented limitation, OQ2). The CIP formula and day-count are real even though the curve
  is simplified — so the hedge price is *structurally honest*.
- Forwards are a **simulated concept demonstration**, never a live regulated instrument (PRD §9).

---

## 4. Domain model (field-level spec)

The pure domain (no I/O). Types shown as conceptual; Python uses dataclasses/Pydantic + value
objects. `Money = (amount_minor: int, currency: str)`; `Rate = Decimal`.

### 4.1 Value objects
- **`Money`** — `amount_minor: int`, `currency: CurrencyCode`. Ops enforce I5; quantization helpers.
- **`CurrencyCode`** — validated ISO 4217 string + exponent lookup.
- **`CurrencyPair`** — `base: CurrencyCode`, `quote: CurrencyCode`, conventional orientation; `invert()`.
- **`Rate`** — wrapped `Decimal` with fixed scale; `apply(money) -> Money` (convert), `invert()`.
- **`Bps`** — basis-point value; `of(rate)`, `to_decimal()`.

### 4.2 Entities & aggregates

**`Exposure`** (aggregate root for a declared risk)
| Field | Type | Notes |
|---|---|---|
| `id` | UUID | |
| `agent_id` | str | bound to authenticated agent (NFR-6) |
| `principal_id` | str | on whose behalf |
| `given` | Money | currency+amount the agent has or owes |
| `target_currency` | CurrencyCode | desired currency |
| `direction` | enum `HAVE` \| `OWE` | asset to convert vs liability to cover |
| `timing` | enum `SPOT` \| `FORWARD` | |
| `value_date` | date \| null | required & future for `FORWARD` (FR-2) |
| `status` | enum (see §6) | lifecycle |
| `created_at` | datetime | via Clock port |

**`Decision`** (normalization output — FR-3)
`exposure_id`, `instrument` (`CONVERT` | `HEDGE`), `pair: CurrencyPair`, `route/venue`,
`rationale: str`, `created_at`.

**`Quote`** (FR-4/5)
| Field | Type | Notes |
|---|---|---|
| `id` | UUID | |
| `exposure_id` | UUID | |
| `pair` | CurrencyPair | |
| `mid_rate` | Rate | real mid from `RateFeed` |
| `all_in_rate` | Rate | rate the agent gets after spread/fees |
| `spread_bps` | Bps | |
| `fees` | list[FeeItem] | provider, platform |
| `value_date` | date | spot or forward |
| `instrument` | enum | CONVERT \| HEDGE |
| `forward_points` | Rate \| null | for HEDGE |
| `expires_at` | datetime | TTL (FR-6) |
| `status` | enum `QUOTED`\|`ACCEPTED`\|`EXPIRED` | |
| `created_at` | datetime | |

**`Order`** (execution attempt — FR-7/11)
`id`, `quote_id`, `agent_id`, `idempotency_key` (unique per agent), `status` (see §6),
`created_at`, `updated_at`.

**`Fill`** (FR-7)
`order_id`, `executed_rate: Rate`, `from_amount: Money`, `to_amount: Money`, `value_date: date`,
`venue: str`, `filled_at: datetime`.

**`CostAttribution`** (FR-13/14) — itemized, reconciles to total (I2)
`order_id`, `mid_reference_rate: Rate`, components: `spread`, `provider_fee`, `platform_fee`,
`slippage`, `rounding_residual` — each as `{amount: Money, bps: Bps}` — and `all_in: {amount, bps}`.

**`Position` / `NetExposure`** (FR-16/17)
Per `(agent_id, currency)`: `net_minor: int`, `open_hedges: list[ref]`, `mark_to_market: Money`,
`unrealized_pnl: Money`, `updated_at`.

**`Policy` / `Mandate`** (FR-19/20)
`agent_id`, `max_single_ticket: Money`, `max_aggregate_net_exposure: Money`,
`allowed_pairs: set`, `allowed_instruments: set`, `window_caps: {...}`,
`approval_threshold: Money` (above → requires human sign-off).

**`AuditEvent`** (FR-23/24) — append-only
`id` (monotonic), `agent_id`, `event_type`, `subject_type`, `subject_id`, `payload: json`,
`occurred_at`, `correlation_id`.

**`LedgerEntry`** (double-entry — FR-25)
`id`, `account`, `debit: Money`/`credit: Money`, `ref_type`, `ref_id`, `posted_at`; entries for an
operation must **balance**.

---

## 5. Domain services (pure)

- **`DecisionEngine`** — `Exposure → Decision`. `timing=SPOT` → `CONVERT`; `timing=FORWARD` →
  `HEDGE`; selects pair orientation; produces rationale (FR-3).
- **`Pricer` / `QuoteEngine`** — builds a `Quote` from a `Decision` + mid (from `RateFeed`) +
  spread/fee config (+ `RateCurve` for forwards). Computes `all_in_rate`, `forward_points`, TTL
  (FR-4/9).
- **`CostAttributor`** — given quote + fill, produces `CostAttribution` and asserts I2 (FR-13/14).
- **`PolicyEngine`** — `(Decision, Quote, current NetExposure, Policy) → Allow | Reject(reason) |
  RequiresApproval` (FR-19–22). Pure; pre-trade.
- **`SlippageModel`** — given quote rate + execution-time mid, yields the fill rate and slippage
  component (FR-12). Deterministic under injected randomness/Clock for tests.
- **`Netting` / `MarkToMarket`** — updates net exposure; revalues open positions at current mid
  (FR-16/17).

All take inputs explicitly (no hidden I/O), enabling property tests and reuse across HTTP/MCP.

---

## 6. State machines

**Exposure**
```
DECLARED → DECIDED → QUOTED → NEUTRALIZED
                         ↘ EXPIRED (quote TTL lapsed, no accept)
   any → CANCELLED (agent cancels before execution)
```

**Quote:** `QUOTED → ACCEPTED → (consumed by Order)` | `QUOTED → EXPIRED`.

**Order** (FR-7/11)
```
CREATED → SUBMITTED → FILLED
                    ↘ REJECTED  (policy / validation, pre-execution)
                    ↘ FAILED    (venue error; safe to retry with same idempotency key)
```
Transitions are guarded; illegal transitions raise. Each transition emits an `AuditEvent`.

---

## 7. Idempotency & concurrency (FR-11, NFR-2)

- Execute requests **must** carry an **`Idempotency-Key`** (HTTP header / MCP arg), unique per
  agent + logical operation.
- **Storage:** unique constraint on `(agent_id, idempotency_key)`. First request creates the `Order`;
  a repeat returns the **stored result** of the original (same fill or same error), never re-executing.
- **Concurrency:** position/net-exposure updates use **optimistic concurrency** (version column) or
  `SELECT … FOR UPDATE` within the execution transaction so two concurrent executes can't both pass
  an aggregate-exposure check.
- **Atomicity (NFR-5):** order creation, fill, ledger entries, position update, and audit event are
  written in **one DB transaction**; partial failure rolls back fully. Outbound side effects (none
  with the simulator; a real venue later) use a **transactional outbox** pattern.
- Quote acceptance validates **TTL server-side** (FR-6): an expired quote → structured error, no
  execution.

---

## 8. API specifications

HTTP and MCP are thin adapters over the **same application use-cases** (FR-28). Schemas are shared
Pydantic models; MCP tool schemas are generated from them.

### 8.1 HTTP / JSON (OpenAPI) — FR-26
Auth: per-agent API key (`Authorization: Bearer <key>`) resolving to `agent_id` + mandate (NFR-6).

| Method | Path | Purpose | Key in/out |
|---|---|---|---|
| `POST` | `/exposures` | Declare exposure; returns exposure + decision | FR-1/3 |
| `POST` | `/quotes` | Request a quote for an exposure | FR-4/5 |
| `POST` | `/orders` | Execute an accepted quote (header `Idempotency-Key`) | FR-7/11 |
| `GET` | `/orders/{id}` | Order status + fill + attribution | FR-7/15 |
| `GET` | `/orders/{id}/cost` | Cost breakdown alone | FR-15 |
| `GET` | `/positions` | Net exposure + MTM per currency | FR-16/17 |
| `GET` | `/audit` | Query audit trail (filters) | FR-24 |
| `PUT` | `/policies/{agent_id}` | Set/replace mandate (admin) | FR-19/20 |
| `POST` | `/orders/{id}/approve` | Human sign-off for over-threshold orders | FR-20 |

Example — declare → returns decision:
```jsonc
// POST /exposures
{ "given": {"amount_minor": 420000, "currency": "EUR"},
  "target_currency": "USD", "direction": "OWE",
  "timing": "FORWARD", "value_date": "2026-07-15" }
// 201
{ "exposure_id": "…", "decision": { "instrument": "HEDGE", "pair": "EUR/USD",
  "rationale": "Future-dated OWE in EUR vs USD book → forward hedge" } }
```

Example — fill with attribution:
```jsonc
// GET /orders/{id}
{ "order": {"status": "FILLED"},
  "fill": { "executed_rate": "1.0853000000",
            "from_amount": {"amount_minor": 420000, "currency": "EUR"},
            "to_amount":   {"amount_minor": 455826, "currency": "USD"},
            "value_date": "2026-07-15", "venue": "sim" },
  "cost_attribution": {
    "mid_reference_rate": "1.0850000000",
    "components": {
      "spread":       {"amount": {"amount_minor": 105, "currency": "USD"}, "bps": "2.5"},
      "provider_fee": {"amount": {"amount_minor": 50,  "currency": "USD"}, "bps": "1.1"},
      "platform_fee": {"amount": {"amount_minor": 25,  "currency": "USD"}, "bps": "0.5"},
      "slippage":     {"amount": {"amount_minor": 13,  "currency": "USD"}, "bps": "0.3"},
      "rounding_residual": {"amount": {"amount_minor": 0, "currency": "USD"}, "bps": "0.0"}
    },
    "all_in": {"amount": {"amount_minor": 193, "currency": "USD"}, "bps": "4.4"} } }
// invariant: components sum exactly to all_in (I2)
```

### 8.2 MCP tools — FR-27
Mirror the use-cases as schema'd tools with structured outputs:

| Tool | Maps to | Notes |
|---|---|---|
| `declare_exposure` | `POST /exposures` | returns exposure + decision |
| `get_quote` | `POST /quotes` | returns quote w/ TTL |
| `execute_hedge` | `POST /orders` | requires `idempotency_key` arg |
| `get_order` / `get_cost_breakdown` | `GET /orders/{id}[/cost]` | |
| `get_position` | `GET /positions` | |
| `get_policy` | read mandate | |

Tool descriptions, input schemas, and structured outputs are derived from the shared DTOs so HTTP
and MCP can't drift (FR-28).

### 8.3 Error model (FR-22, FR-6)
Structured, recoverable, machine-readable. Stable `code`, human `message`, optional `details`.

```jsonc
{ "error": { "code": "POLICY_LIMIT_EXCEEDED",
             "message": "Trade exceeds max aggregate net exposure",
             "details": {"limit": {"amount_minor": 1000000, "currency": "USD"},
                         "would_be": {"amount_minor": 1200000, "currency": "USD"}},
             "recoverable": true, "correlation_id": "…" } }
```

Representative codes: `UNKNOWN_CURRENCY`, `PAIR_NOT_ALLOWED`, `INVALID_VALUE_DATE`,
`QUOTE_EXPIRED`, `POLICY_LIMIT_EXCEEDED`, `APPROVAL_REQUIRED`, `IDEMPOTENCY_CONFLICT`,
`VENUE_UNAVAILABLE`, `RECONCILIATION_MISMATCH`. HTTP maps these to appropriate status codes
(422/409/402/503 etc.); MCP returns them as structured tool errors.

---

## 9. Data model (persistence)

Tables (PostgreSQL; SQLite-compatible): `exposures`, `decisions`, `quotes`, `orders`, `fills`,
`cost_attributions`, `positions`, `policies`, `ledger_entries`, `audit_events`, `idempotency_keys`,
`approvals`.

Key rules:
- **Money columns:** `amount_minor BIGINT NOT NULL` + `currency CHAR(3) NOT NULL` (never `FLOAT`).
- **Rate columns:** `NUMERIC(20,10)`.
- **`audit_events`:** append-only (no `UPDATE`/`DELETE` granted to the app role); monotonic `id`.
- **`idempotency_keys`:** `UNIQUE(agent_id, idempotency_key)` → stores the result reference.
- **`ledger_entries`:** per operation, sum(debits) == sum(credits) (enforced in tx + checked).
- **Concurrency:** `positions.version` for optimistic locking.
- **Migrations:** Alembic; every schema change reviewed.

---

## 10. Ports (interfaces the core depends on) — NFR-7

- **`ExecutionVenue`** *(the seam, FR-29/30)* — `quote(decision) -> VenueQuote`,
  `execute(accepted_quote, idempotency_key) -> VenueFill`, `status(ref) -> VenueStatus`.
- **`RateFeed`** — `get_mid(pair, as_of) -> Rate` (real feed adapter + sim adapter).
- **`RateCurve`** — `rate(currency, tenor) -> Decimal` (flat/sim in v1).
- **`BusinessCalendar`** — `is_business_day(date)`, `add_business_days(date, n)`.
- **`Clock`** — `now() -> datetime` (deterministic in tests, NFR-8).
- **Repositories** — `ExposureRepo`, `QuoteRepo`, `OrderRepo`, `FillRepo`, `PositionRepo`,
  `PolicyRepo`, `AuditRepo`, `LedgerRepo`, `IdempotencyStore` — each an interface with Postgres,
  SQLite, and in-memory implementations.

The simulator→real swap touches only the `ExecutionVenue` adapter (FR-30, SM-5).

---

## 11. Testing strategy (NFR-1, NFR-8, FR-14, FR-30)

- **Unit (pure domain):** every domain service with explicit inputs.
- **Property-based (Hypothesis):** money-math invariants I1–I5; attribution sum-to-total (I2);
  inversion round-trip (I4); policy never lets aggregate exceed limit.
- **Golden tests:** fixed exposures → exact expected cost-attribution breakdowns (regression guard).
- **Venue contract tests (FR-30):** one suite that **any** `ExecutionVenue` impl must pass — run
  against the simulator now; the proof that a real partner is a contained swap (SM-5).
- **Integration:** HTTP (FastAPI test client) and MCP adapters → assert behavioral parity (FR-28,
  SM-6); idempotent-retry test (FR-11); over-limit rejection test (FR-21, SM-3).
- **Determinism:** injected `Clock`, seeded `SlippageModel`, sim `RateFeed` for reproducibility.

---

## 12. Observability & security

**Observability (NFR-9):**
- Structured JSON logs with a **`correlation_id`** threaded from inbound request → audit events.
- Metrics: quote latency, fill slippage distribution, policy-rejection rate, idempotency-hit rate,
  reconciliation status.
- The audit trail (FR-23) doubles as the authoritative event history.

**Security & authority (NFR-6):**
- Per-agent **API keys** (v1, OQ3) → `agent_id` + bound mandate; secrets never logged.
- **Mandates enforced server-side** (FR-21) — the API exposes no way to widen one's own authority.
- Approval flow (FR-20): over-threshold orders park in `APPROVAL_REQUIRED` until a privileged
  `approve` call.
- Input validation at the adapter edge (Pydantic) + domain invariants; defense in depth.
- No real funds, no PII custody in this build (PRD §9).

---

## 13. Known limitations (v1, documented on purpose)
- Weekend-only business calendar; no holiday calendars (OQ4).
- Flat/simulated interest-rate curves for forward pricing (OQ2) — formula is real, curve is simple.
- Single free FX feed for the mid with caching + fallback (OQ1).
- Simulated execution only; **no real money, no licensed partner** (PRD §9/§11).
