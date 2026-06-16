"""Domain object <-> JSON-able dict codec (persistence adapter only).

Encoding is value-driven; decoding is **annotation-driven** — because the target dataclass and its
field types are known, encoded values need no type tags. The domain stays pure: it has no
knowledge of serialization.
"""

from __future__ import annotations

import dataclasses
import types
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

from ...domain.currency import CurrencyPair
from ...domain.money import Money
from ...domain.rate import Bps, Rate


def encode(value: Any) -> Any:
    """Turn a domain value into a JSON-serializable structure."""
    if value is None:
        return None
    if isinstance(value, Enum):  # StrEnum etc. -> its underlying value
        return value.value
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float | str):
        return value
    if isinstance(value, Money):
        return {"amount_minor": value.amount_minor, "currency": value.currency}
    if isinstance(value, Rate | Bps):
        return format(value.value, "f")
    if isinstance(value, CurrencyPair):
        return f"{value.base}/{value.quote}"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):  # check before date (datetime subclasses date)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, frozenset | set):
        return sorted(encode(v) for v in value)
    if isinstance(value, list | tuple):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if dataclasses.is_dataclass(value):
        return {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
    raise TypeError(f"Cannot encode value of type {type(value)!r}")


def decode(tp: Any, value: Any) -> Any:
    """Reconstruct a value of type ``tp`` from its encoded form."""
    if value is None:
        return None
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        (inner,) = [a for a in get_args(tp) if a is not type(None)][:1] or (Any,)
        return decode(inner, value)
    if origin in (list, tuple):
        args = get_args(tp)
        elem = args[0] if args else Any
        return [decode(elem, v) for v in value]
    if origin in (frozenset, set):
        args = get_args(tp)
        elem = args[0] if args else Any
        return frozenset(decode(elem, v) for v in value)
    if origin is dict:
        return dict(value)
    if tp is Money:
        return Money(value["amount_minor"], value["currency"])
    if tp is Rate:
        return Rate(Decimal(value))
    if tp is Bps:
        return Bps(Decimal(value))
    if tp is CurrencyPair:
        return CurrencyPair.parse(value)
    if tp is Decimal:
        return Decimal(value)
    if tp is datetime:
        return datetime.fromisoformat(value)
    if tp is date:
        return date.fromisoformat(value)
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(value)
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        hints = get_type_hints(tp)
        kwargs = {
            f.name: decode(hints[f.name], value[f.name])
            for f in dataclasses.fields(tp) if f.name in value
        }
        return cast(Any, tp)(**kwargs)
    return value  # primitives / Any
