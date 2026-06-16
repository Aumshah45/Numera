"""Money value object and ISO 4217 currency data.

Conventions (TRD §2 — non-negotiable):

* Amounts are stored as **integer minor units** plus an ISO 4217 currency code. No floats, ever.
* Computation uses :class:`decimal.Decimal` under an explicit context.
* Rounding to minor units uses ``ROUND_HALF_EVEN`` (banker's rounding).
* Arithmetic across different currencies raises (invariant I5).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal
from typing import Final

from .errors import CurrencyMismatch, UnknownCurrency

#: Working precision for all monetary/rate computation (TRD §2.2).
MONEY_CONTEXT: Final = Context(prec=28, rounding=ROUND_HALF_EVEN)

#: ISO 4217 minor-unit exponents for supported currencies. A currency is supported iff it is
#: present here. Extend deliberately — quoting an unknown currency must fail loudly.
MINOR_UNIT_EXPONENT: Final[dict[str, int]] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "INR": 2,
    "JPY": 0,
    "CHF": 2,
    "CAD": 2,
    "AUD": 2,
    "NZD": 2,
    "CNY": 2,
    "SGD": 2,
    "HKD": 2,
    "SEK": 2,
    "NOK": 2,
    "DKK": 2,
    "PLN": 2,
    "ZAR": 2,
    "AED": 2,
    "BHD": 3,  # 3-decimal currency, included on purpose to exercise the exponent logic
    "KWD": 3,
}


def is_supported(currency: str) -> bool:
    return currency in MINOR_UNIT_EXPONENT


def minor_unit_exponent(currency: str) -> int:
    """Return the ISO 4217 minor-unit exponent for ``currency``; raise :class:`UnknownCurrency`."""
    try:
        return MINOR_UNIT_EXPONENT[currency]
    except KeyError:
        raise UnknownCurrency(
            f"Unsupported currency: {currency!r}",
            details={"currency": currency},
        ) from None


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """An exact monetary amount: integer ``amount_minor`` in ``currency`` (ISO 4217).

    Examples:
        ``Money(1234, "USD")`` is $12.34. ``Money(1234, "JPY")`` is ¥1234 (exponent 0).
    """

    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount_minor, int) or isinstance(self.amount_minor, bool):
            raise TypeError("amount_minor must be an int (minor units)")
        # Validates the currency is supported; raises UnknownCurrency otherwise.
        minor_unit_exponent(self.currency)

    # -- construction ---------------------------------------------------------------------
    @classmethod
    def from_major(cls, amount: Decimal | int | str, currency: str) -> Money:
        """Build from a major-unit amount (e.g. ``"12.34"`` USD), rounding to the minor unit.

        Uses banker's rounding. ``Money.from_major("12.345", "USD")`` → ``Money(1234, "USD")``.
        """
        exp = minor_unit_exponent(currency)
        value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
        quantum = Decimal(1).scaleb(-exp)  # 10**-exp, e.g. Decimal("0.01")
        rounded = value.quantize(quantum, rounding=ROUND_HALF_EVEN, context=MONEY_CONTEXT)
        minor = rounded.scaleb(exp).to_integral_value(rounding=ROUND_HALF_EVEN)
        return cls(int(minor), currency)

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(0, currency)

    # -- views ----------------------------------------------------------------------------
    @property
    def exponent(self) -> int:
        return minor_unit_exponent(self.currency)

    def as_decimal(self) -> Decimal:
        """Return the major-unit value as an exact ``Decimal`` (e.g. ``Decimal("12.34")``)."""
        return Decimal(self.amount_minor).scaleb(-self.exponent)

    # -- arithmetic (same-currency only, invariant I5) ------------------------------------
    def _check_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(
                f"Cannot combine {self.currency} with {other.currency}",
                details={"left": self.currency, "right": other.currency},
            )

    def __add__(self, other: Money) -> Money:
        self._check_same_currency(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check_same_currency(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount_minor, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.amount_minor), self.currency)

    # comparisons (same-currency)
    def __lt__(self, other: Money) -> bool:
        self._check_same_currency(other)
        return self.amount_minor < other.amount_minor

    def __le__(self, other: Money) -> bool:
        self._check_same_currency(other)
        return self.amount_minor <= other.amount_minor

    def __gt__(self, other: Money) -> bool:
        self._check_same_currency(other)
        return self.amount_minor > other.amount_minor

    def __ge__(self, other: Money) -> bool:
        self._check_same_currency(other)
        return self.amount_minor >= other.amount_minor

    # -- formatting -----------------------------------------------------------------------
    def __str__(self) -> str:
        return f"{self.as_decimal()} {self.currency}"
