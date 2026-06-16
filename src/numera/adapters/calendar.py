"""Business-calendar adapter (implements :class:`numera.ports.BusinessCalendar`).

v1 is **weekend-only**: Saturday and Sunday are non-business days. Full holiday calendars are a
documented limitation (OQ4) — they would slot in behind this same port without changing callers.
"""

from __future__ import annotations

from datetime import date, timedelta

_SATURDAY = 5
_SUNDAY = 6


class WeekendCalendar:
    def is_business_day(self, day: date) -> bool:
        return day.weekday() not in (_SATURDAY, _SUNDAY)

    def add_business_days(self, start: date, n: int) -> date:
        """Advance ``n`` business days from ``start`` (following convention)."""
        current = start
        remaining = n
        while remaining > 0:
            current = current + timedelta(days=1)
            if self.is_business_day(current):
                remaining -= 1
        # If start itself is a non-business day and n==0, roll forward to next business day.
        while not self.is_business_day(current):
            current = current + timedelta(days=1)
        return current
