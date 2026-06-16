"""Exchange-rate and basis-point value objects.

A :class:`Rate` is stored as a ``Decimal`` quantised to a fixed scale (TRD §2.2) and means
"QUOTE units per 1 BASE unit". :class:`Rate.convert` applies it to a :class:`Money` amount,
quantising the result to the target currency's minor unit exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

from .money import MONEY_CONTEXT, Money

#: Decimal places at which rates are stored/quoted (TRD §2.2).
RATE_SCALE: Final = 10
_RATE_QUANTUM: Final = Decimal(1).scaleb(-RATE_SCALE)

#: Basis points per unit fraction: 1 bp = 0.0001.
BPS_PER_UNIT: Final = Decimal(10_000)


@dataclass(frozen=True, slots=True)
class Rate:
    """An exchange rate: QUOTE units per 1 BASE unit, e.g. ``Rate("1.0850")`` for EUR/USD."""

    value: Decimal

    def __post_init__(self) -> None:
        v = self.value if isinstance(self.value, Decimal) else Decimal(str(self.value))
        if v <= 0:
            raise ValueError("Rate must be positive")
        object.__setattr__(self, "value", v.quantize(_RATE_QUANTUM, rounding=ROUND_HALF_EVEN))

    @classmethod
    def of(cls, value: Decimal | int | str) -> Rate:
        return cls(Decimal(str(value)))

    def invert(self) -> Rate:
        """Return the reciprocal rate (BASE per QUOTE)."""
        return Rate(MONEY_CONTEXT.divide(Decimal(1), self.value))

    def scaled(self, factor: Decimal) -> Rate:
        """Return this rate multiplied by ``factor`` (e.g. to apply an all-in cost fraction)."""
        return Rate(MONEY_CONTEXT.multiply(self.value, factor))

    def convert(self, amount: Money, to_currency: str) -> Money:
        """Convert ``amount`` (in the base currency) into ``to_currency`` at this rate."""
        converted_major = MONEY_CONTEXT.multiply(amount.as_decimal(), self.value)
        return Money.from_major(converted_major, to_currency)

    def __str__(self) -> str:
        return format(self.value, "f")


@dataclass(frozen=True, slots=True)
class Bps:
    """A basis-point quantity. ``Bps("25")`` == 0.25% == fraction 0.0025."""

    value: Decimal

    def __post_init__(self) -> None:
        v = self.value if isinstance(self.value, Decimal) else Decimal(str(self.value))
        object.__setattr__(self, "value", v)

    @classmethod
    def of(cls, value: Decimal | int | str) -> Bps:
        return cls(Decimal(str(value)))

    @classmethod
    def from_fraction(cls, fraction: Decimal) -> Bps:
        return cls(MONEY_CONTEXT.multiply(fraction, BPS_PER_UNIT))

    def as_fraction(self) -> Decimal:
        """Return the value as a plain fraction (e.g. ``Bps("25").as_fraction()`` → 0.0025)."""
        return MONEY_CONTEXT.divide(self.value, BPS_PER_UNIT)

    def __add__(self, other: Bps) -> Bps:
        return Bps(self.value + other.value)

    def quantized(self, places: int = 2) -> Bps:
        return Bps(self.value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN))

    def __str__(self) -> str:
        return f"{self.value} bps"
