# Numera — Glossary

Plain-English definitions of the FX, finance, and domain terms used across the Numera docs.
If a term in the PRD/TRD/Architecture is unfamiliar, it should be here. Terms are grouped by
theme; within a group they build on each other.

> Read this once top-to-bottom and the rest of the docs will make sense. Everything Numera does
> is ultimately "take a declared exposure, price it honestly, neutralize it, and prove where every
> cent went."

---

## 1. The core idea in one paragraph

A party (here, an **AI agent**) ends up holding money in one currency while it needs money in
another — now or at a known future date. That mismatch is **FX risk**: if the exchange rate moves,
the agent gains or loses value through no decision of its own. **Hedging** is the act of removing
that risk. Numera is an API that lets an agent declare the mismatch ("exposure") and get it
neutralized — either by converting now (**spot**) or by locking a rate for the future
(**forward/hedge**) — and returns an exact, itemized account of what it cost.

---

## 2. Currencies, rates, and quoting

- **Currency** — A unit of money identified by its **ISO 4217** code (e.g. `USD`, `EUR`, `INR`,
  `JPY`). Each code has a defined number of decimal places (its *minor-unit exponent*): USD has 2
  (cents), JPY has 0, BHD (Bahraini dinar) has 3.
- **Minor units** — The smallest indivisible amount of a currency, as an integer. $12.34 USD =
  `1234` minor units. Numera stores all money this way to avoid rounding errors (see *floating-point*
  below). 1 JPY = `1` minor unit; ¥1234 = `1234` minor units (no decimals).
- **Currency pair** — Two currencies being exchanged, written `BASE/QUOTE` (e.g. `EUR/USD`). The
  rate tells you how many units of the **quote** currency one unit of the **base** currency buys.
- **Exchange rate** — The price of the base currency in terms of the quote currency. `EUR/USD =
  1.0850` means 1 EUR = 1.0850 USD.
- **Market convention** — For each pair there is a *conventional* direction it's quoted in (the
  market quotes `EUR/USD`, not `USD/EUR`). To get the reverse rate you **invert** (`1 / rate`).
  Quoting in the wrong direction is a classic bug; Numera's `CurrencyPair` value object encodes the
  convention so this can't happen silently.
- **Mid-market rate (mid)** — The "true" midpoint between what buyers bid and what sellers ask,
  with no markup. This is the fair reference price. Numera pulls the **real** mid from a public feed
  so cost attribution is measured against reality, not an invented number.
- **Bid / Ask (offer)** — The price someone will *buy* at (bid) vs *sell* at (ask). The **mid** is
  halfway between them.
- **Spread** — The gap between the price you actually get and the mid. It's how venues make money.
  Usually expressed in **basis points**. A "wide" spread is expensive; a "tight" spread is cheap.
- **Basis point (bp / bps)** — One hundredth of one percent: `1 bp = 0.01% = 0.0001`. A 25 bps
  spread on a $10,000 conversion costs $25. Finance measures small rate differences in bps because
  percentages get unwieldy. "Where every basis point went" = the cost-attribution promise.
- **Pip** — The smallest conventional increment a pair is quoted in (often the 4th decimal for most
  pairs, 2nd for JPY pairs). Mentioned for completeness; Numera reasons in bps internally.

---

## 3. Time, settlement, and value dates

- **Spot** — A transaction at *today's* rate, settling almost immediately. In Numera, "timing =
  now."
- **Value date / settlement date** — The date the two parties actually exchange the money. It is
  **not** the trade date.
- **T+2** — The standard spot convention: settlement happens **two business days** after the trade
  date (`T`). Some pairs settle `T+1` (e.g. USD/CAD). Weekends and holidays are skipped.
- **Business-day adjustment** — If a calculated value date lands on a weekend/holiday, it rolls to
  the next valid business day (per a convention like "following" / "modified following"). v1 uses a
  weekend-only calendar and flags full holiday calendars as a known limitation.
- **Day-count convention** — The rule for turning a span of calendar days into a *fraction of a
  year* (`τ`, "tau") for interest math. **ACT/360** (actual days ÷ 360) is the default for most
  currencies; **ACT/365** is used for some (e.g. GBP). It matters because forward pricing multiplies
  interest rates by `τ`.

---

## 4. Exposure, conversion, and hedging

- **Exposure** — The thing the agent declares: a currency mismatch it wants neutralized. It has an
  amount, a given currency, a target currency, a **direction**, and a **timing**. Example: "I owe
  4,200 EUR in 30 days, my books are in USD."
- **Direction** — Whether the agent *has* the currency (an asset to convert) or *owes* it (a future
  liability to cover). This determines which way the risk runs.
- **Conversion (spot convert)** — Neutralizing a *now* exposure by exchanging one currency for
  another at the current rate. Simple and immediate.
- **Hedge** — Neutralizing a *future-dated* exposure by locking in a rate **today** for an exchange
  that settles **later**, so a rate move between now and then can't hurt the agent.
- **Forward (FX forward)** — The simplest hedging instrument: an agreement to exchange a set amount
  at a fixed rate on a future value date. The fixed rate is the **forward rate**. (Real forwards are
  regulated derivatives — Numera only *simulates* them; see *Simulated venue*.)
- **Forward rate (F)** — The rate for a future-dated exchange. It is **not** a prediction of the
  future spot rate; it is derived from today's spot and the two currencies' interest rates.
- **Forward points** — The difference between the forward rate and the spot rate (`F − S`). Can be
  positive or negative depending on which currency has the higher interest rate.
- **Covered Interest-Rate Parity (CIP)** — The no-arbitrage rule that sets the forward rate:
  `F = S × (1 + r_quote·τ) / (1 + r_base·τ)`, where `S` is spot, `r_base`/`r_quote` are the two
  currencies' interest rates, and `τ` is the day-count year fraction. Intuition: holding the
  higher-interest currency should not be a free lunch, so the forward rate adjusts to cancel the
  interest-rate advantage. This is how Numera prices hedges *honestly* instead of guessing.
- **Interest-rate curve** — The set of interest rates for a currency across different maturities.
  v1 uses simple flat/simulated rates behind a swappable port.

---

## 5. Execution, fills, and cost

- **Quote** — A price offer for a specific exposure, valid for a limited time. Includes the mid
  reference, the all-in rate the agent would get, the spread/fees, an **expiry (TTL)**, and the
  value date. The agent can *accept* a quote to execute, or let it expire.
- **TTL (time-to-live) / expiry** — How long a quote is honored before its price is stale and must
  be re-requested. Rates move, so quotes can't live forever.
- **Order / Execution** — The act of accepting a quote and actually doing the exchange.
- **Fill** — The result of an execution: the rate actually achieved, the amounts, the value date,
  the venue, and a timestamp. "Filled" = the trade happened.
- **Slippage** — The gap between the rate you were *quoted* and the rate you actually *filled* at,
  caused by the market moving in the moment between accepting and executing. Numera models this
  explicitly and attributes it as its own line item rather than hiding it.
- **Cost attribution** — Numera's signature output: a complete, itemized breakdown of the
  difference between the fair mid rate and what the agent actually paid — mid reference, spread,
  provider fee, platform fee, and slippage — in both absolute money and bps, that **sums exactly to
  the all-in cost**. No hidden markup. This sum-to-total relationship is a tested invariant.
- **All-in rate / all-in cost** — The single effective rate (and total cost) the agent really got
  after every spread, fee, and slippage component is included. The "honest" number.

---

## 6. Positions, risk, and accounting

- **Position** — The net amount of a currency an agent is holding or owes after all its activity.
- **Net exposure** — Per agent, per currency: how much FX risk is still outstanding (open hedges +
  unconverted balances). Numera tracks this so policy limits can be enforced against the *aggregate*,
  not just one trade at a time.
- **Mark-to-market (MTM)** — Revaluing an open position or hedge at the *current* market rate to see
  its present worth. The difference vs the original rate is **unrealized P&L**.
- **P&L (profit and loss)** — Gain or loss. **Realized** P&L is locked in once a trade settles;
  **unrealized** P&L is the paper gain/loss on still-open positions (from MTM).
- **Double-entry ledger** — An accounting discipline where every movement is recorded as balanced
  debits and credits, so the books always reconcile. Numera uses a double-entry-style ledger (even
  for simulated money) for auditability and correctness.
- **Reconciliation** — Checking that two independent records agree — here, Numera's internal ledger
  vs the venue's confirmation of what executed. A mismatch is a red flag surfaced as an error.

---

## 7. Safety, control, and audit

- **Policy / Mandate** — The rules an agent operates under: max single-trade size, max aggregate net
  exposure, allowed currency pairs and instruments, and **approval thresholds** above which a human
  must sign off. Enforced **server-side** so a buggy or compromised agent cannot exceed its authority.
- **Pre-trade check** — A policy evaluation that happens *before* execution. If it fails, the trade
  is rejected with a structured, recoverable error — money never moves.
- **Guardrail** — Any hard, server-enforced limit that protects the agent's principal from
  excessive risk.
- **Idempotency** — The property that doing the same operation twice has the same effect as doing it
  once. Critical for money: a retried "execute" request must **not** double-execute.
- **Idempotency key** — A unique token the caller attaches to an execute request so Numera can
  recognize a retry and return the original result instead of trading again.
- **Audit trail** — A complete, queryable, **append-only** (never edited or deleted) record of every
  exposure, quote, decision, policy check, and fill — with inputs and outcomes — so a principal or
  auditor can reconstruct exactly what happened and why. Non-negotiable for anything touching
  regulated money.
- **Event-sourced / append-only log** — A storage approach where state changes are recorded as an
  immutable sequence of events. You can always replay history; you can never quietly rewrite it.

---

## 8. System & engineering terms

- **Agent** — Here, an autonomous software/AI agent (not a human trader) that calls Numera
  programmatically to manage its FX risk. The primary user.
- **Principal** — The human or organization on whose behalf an agent acts, and whose risk mandate
  the policy layer enforces.
- **MCP (Model Context Protocol)** — An open protocol that lets AI agents call external tools in a
  standardized way. Numera ships an MCP server so agents can use it as a native tool, alongside a
  conventional HTTP API.
- **HTTP/JSON API** — A conventional typed web API (here, FastAPI + OpenAPI) for developers and
  non-agent integrations.
- **Hexagonal architecture (ports & adapters)** — A design where the pure business logic ("core")
  talks to the outside world only through abstract interfaces ("ports"), and concrete
  implementations ("adapters") plug into those ports. Lets Numera swap a *simulated* venue for a
  *real licensed* one without touching the core.
- **Port** — An abstract interface the core depends on (e.g. `ExecutionVenue`, `RateFeed`,
  `Repository`).
- **Adapter** — A concrete implementation of a port (e.g. `SimulatedVenue`, `RealRateFeed`,
  `PostgresExposureRepo`).
- **The venue seam** — The single, well-isolated boundary (`ExecutionVenue` port) where regulated
  execution happens. Today a simulator sits behind it; a licensed partner can be dropped in later
  with contained change. This seam is the central architectural idea.
- **Simulated venue** — A fake execution backend that behaves like a real FX venue (quotes, fills,
  spreads, slippage) but moves **no real money**. Lets the entire system be built and demoed without
  licensing.
- **Floating-point / float** — A computer's approximate representation of decimal numbers. Using
  floats for money introduces tiny errors that compound — *never* used here. Numera uses integer
  minor units + arbitrary-precision `Decimal`.
- **Banker's rounding (ROUND_HALF_EVEN)** — A rounding rule that rounds a halfway value to the
  nearest *even* digit (2.5 → 2, 3.5 → 4), which avoids the upward bias of "always round half up"
  over many transactions. Numera's documented rounding mode.
- **Property-based testing** — Testing that asserts *invariants* hold across many auto-generated
  inputs (e.g. "cost components always sum to the total", "convert-then-invert round-trips"),
  rather than checking a few hand-picked examples. Used for money math via Hypothesis.
- **Contract test** — A shared test suite that any implementation of a port must pass, guaranteeing
  the simulated and (future) real venue behave identically from the core's perspective.

---

## 9. Regulatory terms (why real money is deferred)

- **Money transmission / payments licensing** — Legal authorization required to move or hold other
  people's money. Numera deliberately does **not** hold funds in the solo build.
- **FX dealing / brokerage authorization** — Licensing required to execute FX trades on others'
  behalf.
- **Derivatives regulation** — Forwards and options are regulated financial instruments; offering
  them live requires authorization. Numera only *simulates* the forward concept.
- **KYC / AML** — "Know Your Customer" / "Anti-Money-Laundering": legally required identity checks
  and monitoring on parties. Out of scope for the simulated build.
- **Licensed partner** — A regulated provider whose API actually performs execution. The intended
  real-world path is to sit *on top of* such a partner via the venue seam — never to be the
  regulated entity yourself in the solo build.

> See `docs/PRD.md` §Regulatory & Compliance and `idea.md` for the full constraint. Nothing in these
> docs is legal or financial advice.
