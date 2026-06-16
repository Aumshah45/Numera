"""Currency-pair value object.

A :class:`CurrencyPair` is ``BASE/QUOTE``; a rate for the pair means "QUOTE units per 1 BASE
unit". The pair owns its orientation so quoting direction cannot be inverted by accident
(supports invariant I4). For Numera's spot conversion the base is the currency being sold
(``given``) and the quote is the currency being received (``target``).
"""

from __future__ import annotations

from dataclasses import dataclass

from .money import minor_unit_exponent


@dataclass(frozen=True, slots=True)
class CurrencyPair:
    base: str
    quote: str

    def __post_init__(self) -> None:
        # Validates both currencies are supported (raises UnknownCurrency otherwise).
        minor_unit_exponent(self.base)
        minor_unit_exponent(self.quote)
        if self.base == self.quote:
            raise ValueError("base and quote currencies must differ")

    @classmethod
    def parse(cls, text: str) -> CurrencyPair:
        """Parse ``"EUR/USD"`` into a :class:`CurrencyPair`."""
        base, _, quote = text.partition("/")
        return cls(base.strip().upper(), quote.strip().upper())

    def inverse(self) -> CurrencyPair:
        return CurrencyPair(self.quote, self.base)

    def __str__(self) -> str:
        return f"{self.base}/{self.quote}"
