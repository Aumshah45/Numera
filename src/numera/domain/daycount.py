"""Day-count conventions for turning a span of days into a year fraction τ (TRD §3.2/§3.3).

Each currency accrues money-market interest on its own basis: most use **ACT/360**; a handful
(e.g. GBP) use **ACT/365**. Covered interest-rate parity multiplies each currency's rate by its
own τ, so the basis is looked up per currency.

v1 limitation: a weekend-only business calendar and no holiday calendars (OQ4); the day-count
arithmetic itself is standard.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

#: Currencies whose money-market convention is ACT/365 (everything else defaults to ACT/360).
ACT_365_CURRENCIES: Final[frozenset[str]] = frozenset({"GBP", "AUD", "NZD", "INR", "ZAR", "SGD"})

ACT_360: Final = Decimal(360)
ACT_365: Final = Decimal(365)


def day_count_basis(currency: str) -> Decimal:
    return ACT_365 if currency in ACT_365_CURRENCIES else ACT_360


def year_fraction(currency: str, days: int) -> Decimal:
    """Return τ = days / basis(currency). ``days`` is the actual calendar-day span."""
    if days < 0:
        raise ValueError("day-count span must be non-negative")
    return Decimal(days) / day_count_basis(currency)
