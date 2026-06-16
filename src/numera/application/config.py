"""Runtime configuration (12-factor, env-driven) via pydantic-settings.

Selects which adapters are wired (e.g. the rate feed) and the venue/platform economics. Override
any field with an env var prefixed ``NUMERA_`` (e.g. ``NUMERA_RATE_FEED=real``).
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NUMERA_", env_file=".env", extra="ignore")

    # Adapter selection
    rate_feed: Literal["sim", "real"] = "sim"
    frankfurter_base_url: str = "https://api.frankfurter.dev"
    rate_ttl_seconds: int = 60
    persistence: Literal["memory", "sqlite"] = "memory"
    database_url: str = "sqlite:///numera.db"

    # Venue economics (simulated)
    venue_spread_bps: str = "25"
    venue_provider_fee_bps: str = "10"
    spot_lag_days: int = 2

    # Slippage model for the simulated venue (FR-12). Default "none" keeps fills deterministic.
    slippage_mode: Literal["none", "fixed", "seeded"] = "none"
    slippage_bps: str = "0"
    slippage_seed: int = 1
    slippage_max_adverse_bps: str = "8"
    slippage_max_favorable_bps: str = "3"

    # Numera economics
    platform_fee_bps: str = "5"
    quote_ttl_seconds: int = 120
