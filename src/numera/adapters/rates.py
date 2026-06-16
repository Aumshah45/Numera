"""Rate-feed and rate-curve adapters.

* :class:`SimRateFeed` — deterministic mids for tests/offline demos (NFR-8).
* :class:`RealRateFeed` — real mid-market rates from the free Frankfurter API (ECB data), with a
  short TTL cache and last-good fallback (ARCHITECTURE §7, ADR-005). Spread/slippage/fees are NOT
  applied here — the feed only supplies the honest reference mid.
* :class:`FlatRateCurve` — placeholder interest-rate curve for forward pricing (Phase 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import httpx

from ..domain.currency import CurrencyPair
from ..domain.errors import RateUnavailable
from ..domain.rate import Rate
from ..ports import Clock


class SimRateFeed:
    """Returns mids from a static table; inverts when only the reverse pair is known."""

    def __init__(self, mids: dict[str, Decimal] | None = None) -> None:
        # Keys are "BASE/QUOTE". A small, sensible default set for demos/tests.
        self._mids: dict[str, Rate] = {
            k: Rate.of(v)
            for k, v in (mids or {
                "EUR/USD": Decimal("1.0850"),
                "GBP/USD": Decimal("1.2700"),
                "USD/INR": Decimal("83.20"),
                "USD/JPY": Decimal("156.50"),
                "EUR/GBP": Decimal("0.8540"),
                "USD/CHF": Decimal("0.9050"),
                "AUD/USD": Decimal("0.6650"),
            }).items()
        }

    def get_mid(self, pair: CurrencyPair) -> Rate:
        direct = self._mids.get(str(pair))
        if direct is not None:
            return direct
        inverse = self._mids.get(str(pair.inverse()))
        if inverse is not None:
            return inverse.invert()
        raise RateUnavailable(
            f"No simulated mid for {pair}", details={"pair": str(pair)}
        )


@dataclass(frozen=True, slots=True)
class _Cached:
    rate: Rate
    fetched_at: datetime


class RealRateFeed:
    """Fetches the real mid from Frankfurter (https://frankfurter.dev), cached with a TTL."""

    def __init__(
        self,
        clock: Clock,
        *,
        base_url: str = "https://api.frankfurter.dev",
        ttl_seconds: int = 60,
        max_stale_seconds: int = 3600,
        timeout_seconds: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._clock = clock
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max_stale = timedelta(seconds=max_stale_seconds)
        self._client = client or httpx.Client(
            base_url=base_url, timeout=timeout_seconds, follow_redirects=True
        )
        self._cache: dict[str, _Cached] = {}

    def get_mid(self, pair: CurrencyPair) -> Rate:
        now = self._clock.now()
        key = str(pair)
        cached = self._cache.get(key)
        if cached is not None and now - cached.fetched_at <= self._ttl:
            return cached.rate

        try:
            resp = self._client.get(
                "/v1/latest", params={"base": pair.base, "symbols": pair.quote}
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["rates"][pair.quote]
            rate = Rate.of(Decimal(str(raw)))
            self._cache[key] = _Cached(rate=rate, fetched_at=now)
            return rate
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            # Fall back to last-good within the freshness window; otherwise fail loudly.
            if cached is not None and now - cached.fetched_at <= self._max_stale:
                return cached.rate
            raise RateUnavailable(
                f"Could not fetch mid for {pair}: {exc}",
                details={"pair": str(pair)},
            ) from exc


class FlatRateCurve:
    """A flat per-currency interest-rate curve for forward pricing (CIP).

    v1 limitation (OQ2): rates are flat across tenors and simulated — only the *shape* of the
    forward formula is real, not a live curve. Defaults are plausible mid-2020s annual rates.
    """

    _DEFAULTS: dict[str, Decimal] = {
        "USD": Decimal("0.0525"), "EUR": Decimal("0.0375"), "GBP": Decimal("0.0500"),
        "JPY": Decimal("0.0010"), "INR": Decimal("0.0665"), "CHF": Decimal("0.0150"),
        "CAD": Decimal("0.0475"), "AUD": Decimal("0.0435"), "SGD": Decimal("0.0360"),
    }

    def __init__(self, rates: dict[str, Decimal] | None = None, default: Decimal = Decimal("0.03")):
        self._rates = {**self._DEFAULTS, **(rates or {})}
        self._default = default

    def rate(self, currency: str, tenor_days: int) -> Decimal:
        return self._rates.get(currency, self._default)
