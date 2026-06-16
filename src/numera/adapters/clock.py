"""Clock adapters (implements :class:`numera.ports.Clock`)."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Real wall-clock time in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Deterministic clock for tests (NFR-8). Time only advances when explicitly set."""

    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def set(self, moment: datetime) -> None:
        self._moment = moment

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._moment = self._moment + timedelta(seconds=seconds)
