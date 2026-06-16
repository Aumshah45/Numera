"""Slippage models for the simulated venue (FR-12).

Slippage is the gap between the quoted rate and the rate actually filled, because the market moves
between accepting a quote and executing it. A positive value is **adverse** to the agent (it
receives fewer target units); a negative value is favourable.

Real venues don't "model" slippage — it just happens. These models exist only so the *simulator*
can produce realistic, **reproducible** behaviour. ``SeededSlippage`` is deterministic given the
idempotency key, so a retried execution yields the same fill (consistent with idempotency).
"""

from __future__ import annotations

import hashlib
import random
from decimal import Decimal
from typing import Protocol

from ..domain.rate import Bps


class SlippageModel(Protocol):
    def sample(self, idempotency_key: str) -> Bps:
        """Return signed slippage in bps (positive = adverse to the agent)."""
        ...


class NoSlippage:
    """Deterministic zero slippage (Phase 1 behaviour; default)."""

    def sample(self, idempotency_key: str) -> Bps:
        return Bps.of(0)


class FixedSlippage:
    """Always the same slippage — handy for deterministic demos/tests."""

    def __init__(self, bps: Bps) -> None:
        self._bps = bps

    def sample(self, idempotency_key: str) -> Bps:
        return self._bps


class SeededSlippage:
    """Reproducible pseudo-random slippage in ``[-max_favorable, +max_adverse]`` bps.

    Keyed by the idempotency key so the same execution always yields the same slippage.
    """

    def __init__(self, seed: int, max_adverse_bps: Decimal, max_favorable_bps: Decimal) -> None:
        self._seed = seed
        self._max_adverse = max_adverse_bps
        self._max_favorable = max_favorable_bps

    def sample(self, idempotency_key: str) -> Bps:
        digest = hashlib.sha256(f"{self._seed}:{idempotency_key}".encode()).digest()
        rng = random.Random(int.from_bytes(digest[:8], "big"))
        draw = rng.uniform(-float(self._max_favorable), float(self._max_adverse))
        return Bps.of(Decimal(str(round(draw, 2))))
