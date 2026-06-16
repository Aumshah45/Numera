"""Money value-object invariants (NFR-1, invariants I1/I3/I5) via property-based testing."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from numera.domain.errors import CurrencyMismatch, UnknownCurrency
from numera.domain.money import MINOR_UNIT_EXPONENT, Money

CURRENCIES = sorted(MINOR_UNIT_EXPONENT)
amounts = st.integers(min_value=-10**12, max_value=10**12)
currencies = st.sampled_from(CURRENCIES)


@given(amount=amounts, currency=currencies)
def test_minor_unit_roundtrip(amount: int, currency: str) -> None:
    """I1: as_decimal()/from_major() round-trips exactly at the currency's minor unit."""
    m = Money(amount, currency)
    assert Money.from_major(m.as_decimal(), currency).amount_minor == amount


@given(a=amounts, b=amounts, currency=currencies)
def test_addition_is_exact(a: int, b: int, currency: str) -> None:
    assert (Money(a, currency) + Money(b, currency)).amount_minor == a + b
    assert (Money(a, currency) - Money(b, currency)).amount_minor == a - b


@given(a=amounts, b=amounts)
def test_cross_currency_arithmetic_raises(a: int, b: int) -> None:
    """I5: combining different currencies is forbidden."""
    with pytest.raises(CurrencyMismatch):
        _ = Money(a, "USD") + Money(b, "EUR")


def test_no_float_amounts() -> None:
    """I3: minor units must be ints; floats are rejected at the boundary."""
    with pytest.raises(TypeError):
        Money(12.34, "USD")  # type: ignore[arg-type]


def test_unknown_currency_rejected() -> None:
    with pytest.raises(UnknownCurrency):
        Money(100, "XYZ")


def test_bankers_rounding() -> None:
    # ROUND_HALF_EVEN: 12.345 -> 12.34, 12.355 -> 12.36 (round half to even)
    assert Money.from_major(Decimal("12.345"), "USD").amount_minor == 1234
    assert Money.from_major(Decimal("12.355"), "USD").amount_minor == 1236


def test_jpy_zero_decimals() -> None:
    assert Money.from_major("1234", "JPY").amount_minor == 1234
    assert Money(1234, "JPY").as_decimal() == Decimal("1234")


def test_bhd_three_decimals() -> None:
    assert Money.from_major("1.234", "BHD").amount_minor == 1234
