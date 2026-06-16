"""CurrencyPair and Rate behaviour, including the conversion round-trip (invariant I4)."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from numera.domain.currency import CurrencyPair
from numera.domain.money import Money
from numera.domain.rate import Rate


def test_pair_parse_and_inverse() -> None:
    pair = CurrencyPair.parse("EUR/USD")
    assert (pair.base, pair.quote) == ("EUR", "USD")
    assert str(pair.inverse()) == "USD/EUR"


def test_rate_convert() -> None:
    rate = Rate.of("83.20")  # INR per USD
    converted = rate.convert(Money.from_major("100", "USD"), "INR")
    assert converted == Money.from_major("8320.00", "INR")


# mid in a realistic FX range [0.50, 200.00]; amounts up to 1,000,000 major units.
# (Degenerate tiny rates would round small conversions to zero, which is not a round-trip.)
mids = st.integers(min_value=50, max_value=20_000).map(lambda n: Decimal(n) / Decimal(100))
majors = st.integers(min_value=1, max_value=10**6)


@given(mid=mids, major=majors)
def test_convert_invert_roundtrip(mid: Decimal, major: int) -> None:
    """I4: convert USD->INR then back stays within a tiny rounding residual."""
    rate = Rate(mid)
    start = Money.from_major(Decimal(major), "USD")
    there = rate.convert(start, "INR")
    back = rate.invert().convert(there, "USD")
    # Allow a few minor units for the two quantisation steps.
    assert abs(back.amount_minor - start.amount_minor) <= 3
