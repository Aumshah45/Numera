# Numera — Product Requirements Document (PRD)

**Status:** Draft v1 · **Owner:** Solo build · **Last updated:** 2026-06-15
**Related:** [`idea.md`](../idea.md) · [`plan.md`](../plan.md) · [`TRD`](./TRD.md) · [`ARCHITECTURE`](./ARCHITECTURE.md) · [`GLOSSARY`](./GLOSSARY.md) · [`DECISIONS`](./DECISIONS.md)

> This document defines **what** Numera is and **why**, and the requirements it must satisfy. The
> **how** lives in the TRD and Architecture. Nothing here is legal or financial advice; see
> §9 Regulatory & Compliance.

---

## 1. Summary

**Numera** is an **agent-first FX and hedging micro-execution API**. An autonomous agent declares a
currency **exposure** — *"I owe 4,200 EUR in 30 days"* or *"I just got paid 1,800 USD but my books
are in INR"* — and Numera:

1. **normalizes** it into the right primitive (immediate **convert** vs future-dated **hedge**),
2. **prices** it against a **real mid-market rate**,
3. **neutralizes** it through an execution **venue** (simulated now; a licensed partner later),
4. returns a **machine-readable fill with complete cost attribution** — every basis point accounted
   for, and
5. does all of this **inside server-enforced risk policy** with a full **audit trail**.

The product is the **agent-facing abstraction, decisioning, cost-attribution, and policy layer** —
*not* the regulated execution venue. The solo build is fully functional against a **simulated
venue**, architected so a licensed partner can be dropped in behind a clean seam with contained
change.

---

## 2. Problem & motivation

As agents transact across borders — paying suppliers, settling micro-invoices, earning in one
currency and spending in another — they silently accumulate **currency exposure** with no way to
manage it:

- An agent that earns in one currency and spends in another carries **unmanaged FX risk**.
- An agent that commits to pay a foreign amount in the future is exposed to the rate **moving
  against it** before settlement.
- Managing this today requires a **human, a bank/broker relationship, and a trading UI built for
  people** — none of which an agent has.

Existing FX/hedging infrastructure is built around **human workflows**: dashboards, relationship
managers, minimum ticket sizes, and treasury-team onboarding. There is no clean, **small-ticket,
programmatic, agent-readable** way to say *"here's my exposure, neutralize it"* and get back a
structured result. **The capability exists in the financial system; the agent-shaped adapter to it
does not.**

### Why now
- Cross-border agent commerce is plausibly large and is **missing a risk-management primitive**.
- The interface an agent wants is far simpler than a trading terminal — closer to *"convert/hedge
  this"* than *"here's an order book."*
- **Cost transparency** (exactly what spread/fee was paid, and why) is something agents *need* and
  humans are often denied — so an agent-native product can **differentiate on structured cost
  attribution** as a first-class output.

---

## 3. Goals & non-goals

### 3.1 Goals
- G1 — Let an agent **declare an exposure** in one call and receive a structured, neutralized result.
- G2 — Make **cost attribution** the signature feature: complete, itemized, trustworthy, reconciling
  exactly to the all-in cost.
- G3 — Be **safe by default**: server-enforced per-agent risk policy an agent cannot exceed even if
  buggy or compromised.
- G4 — Be **auditable**: every decision and execution reconstructable by a principal or auditor.
- G5 — Keep the **venue seam clean**: swapping the simulator for a licensed partner is a contained
  change, not a rewrite.
- G6 — Expose the capability over **both** a typed HTTP API and an **MCP server** from one core.

### 3.2 Non-goals (for the solo build)
- N1 — **Holding or moving real customer funds.**
- N2 — **Directly executing real FX or derivatives** without a licensed partner.
- N3 — **Speculative trading** or anything beyond *neutralizing a declared exposure*.
- N4 — **Exotic instruments.** Start with spot conversion and the simplest forward-style hedge,
  simulated.
- N5 — A human-facing trading dashboard / GUI. (A minimal demo CLI/console is acceptable.)
- N6 — Real KYC/AML onboarding.

---

## 4. Users & personas

| Persona | Who | Primary jobs | What they need from Numera |
|---|---|---|---|
| **The Agent** (primary) | An autonomous AI/software agent transacting cross-border | Neutralize exposures as they arise; stay within mandate | A declarative, machine-readable interface; structured fills; clear, recoverable errors |
| **The Developer** | Engineer building/operating that agent | Integrate, set policy, debug, audit | Typed API + MCP tools, great docs/schemas, full cost & audit visibility |
| **The Fleet Platform** | Org running many agents transacting internationally | One governed FX/hedging primitive across a fleet | Per-agent policy, aggregate net-exposure tracking, reporting |
| **The Principal / Auditor** | The human/org accountable for the risk | Trust, oversight, compliance | Hard guardrails, approval thresholds, complete append-only audit trail |

---

## 5. Jobs-to-be-done (representative)

- JTBD-1 — *"I have 1,800 USD but my books are in INR — convert it now and tell me exactly what it
  cost."* → **spot convert** + cost attribution.
- JTBD-2 — *"I owe 4,200 EUR in 30 days — lock the rate so I'm not exposed."* → **forward hedge** +
  cost attribution.
- JTBD-3 — *"Quote me first; I'll decide whether to accept."* → **quote with TTL**, then accept.
- JTBD-4 — *"Never let me take on more than X exposure or trade a pair I'm not allowed to."* →
  **policy guardrails**.
- JTBD-5 — *"Show me everything I've done and what I currently owe / am exposed to."* → **positions
  + audit/reporting**.
- JTBD-6 — *"My request timed out; retrying must not double-execute."* → **idempotency**.

---

## 6. Product principles

1. **Declarative, not order-book.** Agents say *what to neutralize*, not *how to trade*. The product
   owns the translation.
2. **Transparency as a feature.** Every fill carries complete cost attribution. No hidden spread.
3. **Safe by default.** Hard limits on exposure size, currencies, and authority, enforced
   server-side. An agent cannot exceed its mandate even if compromised.
4. **Licensed-partner-first.** Assume Numera is an intelligent layer on top of regulated rails, not
   the rails themselves.
5. **Auditable.** Every decision and execution is logged in a form a principal or auditor can
   review — non-negotiable for anything touching regulated money.
6. **Correctness over features.** Bugs here lose money. Precise money math, idempotency, and audit
   are not optional.

---

## 7. Functional requirements

Each requirement has an ID used by the traceability matrix in [`plan.md`](../plan.md). MoSCoW
priority in brackets.

### 7.1 Exposure declaration
- **FR-1 [Must]** Accept an exposure: given currency + amount, target currency, direction
  (have/owe), and timing (`spot` now | future `value_date`).
- **FR-2 [Must]** Validate inputs: known ISO 4217 currencies, allowed pair, sane amount, valid/future
  value date for hedges.
- **FR-3 [Must]** Normalize the exposure into a **decision**: `convert` (spot) vs `hedge` (forward),
  with a machine-readable rationale.

### 7.2 Quoting
- **FR-4 [Must]** Produce a **quote** for a declared exposure: mid reference rate, all-in rate,
  itemized spread/fees, value date, venue, and an **expiry (TTL)**.
- **FR-5 [Must]** Separate **quote** from **execution** — an agent can request a quote, then accept
  it within its TTL.
- **FR-6 [Should]** Re-quote cleanly when a quote has expired; never silently execute on a stale
  price.

### 7.3 Execution & fills
- **FR-7 [Must]** Execute an accepted quote and return a **structured fill**: executed rate,
  amounts, value date, venue, timestamp, status.
- **FR-8 [Must]** Support **spot conversion** end-to-end (simulated).
- **FR-9 [Must]** Support a **future-dated forward-style hedge** end-to-end (simulated), priced via
  covered interest-rate parity.
- **FR-10 [Must]** Clearly distinguish a **conversion** from a **hedge** in both API and data model.
- **FR-11 [Must]** **Idempotency:** a retried execute (same idempotency key) returns the original
  result and never double-executes.
- **FR-12 [Must]** Model **slippage** honestly (quote→fill gap) and attribute it explicitly.

### 7.4 Cost attribution (the signature)
- **FR-13 [Must]** Every fill includes an **itemized cost breakdown**: mid-market reference rate,
  spread, provider fee, platform fee, slippage — in absolute money **and** bps.
- **FR-14 [Must]** The breakdown **reconciles exactly** to the all-in cost (a tested invariant).
- **FR-15 [Should]** Cost breakdown is independently retrievable for any past order.

### 7.5 Positions & risk state
- **FR-16 [Must]** Track **net exposure** per agent × currency, including open hedges.
- **FR-17 [Should]** **Mark-to-market** open positions/hedges against the current mid → unrealized
  P&L.
- **FR-18 [Should]** Report realized cost, outstanding exposure, and history per agent.

### 7.6 Policy & guardrails
- **FR-19 [Must]** Per-agent **policy/mandate**: max single-ticket, max aggregate net exposure,
  allowed currency pairs, allowed instruments, windowed caps.
- **FR-20 [Must]** **Approval thresholds**: above a configured size, require human sign-off before
  execution.
- **FR-21 [Must]** Enforce **all** limits **server-side**, evaluated **pre-trade**; over-limit
  attempts are cleanly **rejected**, never partially executed.
- **FR-22 [Must]** Every limit breach is a **structured, recoverable error** and is logged.

### 7.7 Audit & observability
- **FR-23 [Must]** **Append-only audit trail** of every exposure, quote, decision, policy check, and
  fill, with inputs and outcomes.
- **FR-24 [Must]** Audit trail is **queryable** (by agent, time range, instrument, status).
- **FR-25 [Should]** **Reconciliation** check: internal ledger vs venue confirmation; mismatch
  surfaced as an error.

### 7.8 Interfaces
- **FR-26 [Must]** Expose all capabilities over a typed **HTTP/JSON API** (OpenAPI).
- **FR-27 [Must]** Expose the same use-cases over an **MCP server** with schema'd tools and
  structured outputs.
- **FR-28 [Must]** HTTP and MCP are **thin adapters over one shared core** — behavioral parity, no
  divergent logic.

### 7.9 The venue seam
- **FR-29 [Must]** Execution sits behind an **`ExecutionVenue` port** (`quote` / `execute` /
  `status`) with a **simulated** implementation.
- **FR-30 [Must]** Document precisely what a **licensed partner** must implement; a **contract test
  suite** validates any venue implementation so the simulator→real swap is contained.

---

## 8. Non-functional requirements

- **NFR-1 Correctness (money math) [Must]** — No floating-point for currency. Integer minor units +
  arbitrary-precision decimal, documented rounding (banker's rounding). Verified by property-based
  tests. *(See TRD §Money.)*
- **NFR-2 Idempotency & safety [Must]** — Retries are safe; no operation double-executes or
  double-counts exposure.
- **NFR-3 Auditability [Must]** — Append-only, immutable audit log; nothing is silently editable.
- **NFR-4 Latency [Should]** — Quote/decision p95 < ~300 ms excluding upstream rate-feed latency
  (simulated venue is in-process). Targets refined in TRD.
- **NFR-5 Reliability [Should]** — Transactional writes; partial failures never leave inconsistent
  ledger/position state.
- **NFR-6 Security & authority [Must]** — Per-agent authentication; mandates bound to the agent;
  server-side enforcement; no capability to exceed authority via the API.
- **NFR-7 Portability of the seam [Must]** — Core depends only on ports; swapping adapters
  (venue/rate-feed/DB) requires no core change.
- **NFR-8 Testability [Must]** — Deterministic time (Clock port) and rate source for reproducible
  tests; core is pure and I/O-free.
- **NFR-9 Observability [Should]** — Structured logs with correlation IDs; key metrics (quote
  latency, slippage distribution, policy rejection rate).
- **NFR-10 Documentation [Must]** — A maintained design-decisions log ([`DECISIONS.md`](./DECISIONS.md))
  and clear API/tool schemas.

---

## 9. ⚠️ Regulatory & compliance (the dominant constraint)

**Executing FX transactions and offering hedging on someone's behalf is heavily regulated in
essentially every jurisdiction.** Depending on structure and geography it can implicate
money-transmission/payments licensing, FX dealing/brokerage authorization, derivatives regulation
(forwards/options are regulated instruments), KYC/AML on every party, and counterparty-protection
rules.

**A solo builder almost certainly cannot legally hold customer funds or directly execute regulated
FX/derivatives without licensing or a licensed partner.** This is the central design constraint, not
a footnote. Therefore the product is deliberately scoped so that **Numera itself never holds funds
or executes real trades** in this build:

- Everything runs against a **simulated execution venue** (no real money moves).
- The **`ExecutionVenue` seam** is the *only* place real execution would ever happen, isolated so a
  **licensed partner** can be integrated later.
- The forward "hedge" is a **simulated concept demonstration**, not a live regulated instrument.

**Crossing into real money is a business/legal milestone with its own plan**, gated on qualified
legal counsel and a licensed partner — see §11 Deferred scope. *Nothing in these documents is legal
or financial advice.*

---

## 10. Success metrics

- **SM-1 (G1)** — An agent declares a spot exposure and receives a structured, fully-attributed fill
  end-to-end against the simulator (first demo).
- **SM-2 (G2)** — 100% of fills include a cost breakdown that reconciles to the all-in cost to the
  minor unit (property-tested; zero tolerance beyond defined rounding).
- **SM-3 (G3)** — In adversarial tests, **no** sequence of agent calls can exceed the configured
  mandate; 100% of over-limit attempts are rejected and logged.
- **SM-4 (G4)** — Any historical order can be fully reconstructed (inputs, decision, policy check,
  fill, attribution) from the audit trail alone.
- **SM-5 (G5)** — A second `ExecutionVenue` implementation passes the **same** contract test suite
  with no core changes (proves the seam).
- **SM-6 (G6)** — Identical use-cases succeed over **both** HTTP and MCP with matching results.

---

## 11. Explicitly deferred scope (the regulated frontier)

Out of scope for the solo build; each is gated on legal/partner work:

- Connecting to a **real** FX/payments/brokerage provider.
- **Holding, moving, or custodying** real customer funds.
- Real **KYC/AML** onboarding of principals and counterparties.
- Any **live** forward/option execution (regulated derivatives).
- Going to market with real users.

---

## 12. Risks & open questions

| # | Risk / question | Impact | Mitigation / current stance |
|---|---|---|---|
| R1 | **Regulation gates go-live** | Existential for the *business* | Simulator + clean seam; treat real money as a separate legal milestone (§9, §11) |
| R2 | **Partner dependency** — economics/capabilities bounded by the underlying provider | High (post-licensing) | Keep venue interface minimal & standard; contract tests; defer |
| R3 | **Real money, real consequences** — bugs lose money | High | Precise money math, idempotency, audit, property tests from day one |
| R4 | **Quoting & slippage honesty** | Medium | Explicit slippage modeling and attribution; quote TTLs |
| R5 | **Custody/counterparty** is the regulated part | High | Designed so Numera never holds/moves funds in this build |
| OQ1 | Which **free FX feed** for the real mid, and its rate limits/coverage? | Medium | Choose in TRD; abstract behind `RateFeed` port with caching + fallback |
| OQ2 | Source of **interest-rate curves** for forward pricing | Medium | Flat/simulated behind a port in v1; document as limitation |
| OQ3 | Per-agent **auth model** (API keys vs signed tokens) | Medium | Decide in TRD §Security; start with scoped API keys |
| OQ4 | **Holiday calendars** for value dates | Low | Weekend-only in v1; flag as known limitation |

---

## 13. Out-of-the-box demo (what "done" looks like for the solo build)

A scripted demo in which an agent (via MCP and/or HTTP):
1. declares a **spot** exposure → gets a fully-attributed fill;
2. declares a **future-dated** exposure → gets a fully-attributed simulated **hedge**;
3. attempts an **over-limit** trade → gets a clean, structured rejection;
4. **retries** a timed-out execute → gets the original result, no double-execution;
5. queries **positions + audit** → sees net exposure, MTM, and a complete history.

All simulated, all attributed, all auditable — and architected to drop in a licensed partner behind
the venue seam.
