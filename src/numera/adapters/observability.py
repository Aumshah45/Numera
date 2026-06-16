"""Observability: a small in-process metrics registry and structured JSON logging (NFR-9).

The audit trail (FR-23) is the authoritative *event history*; this module adds runtime
*operational* signals — counters, latencies, and correlation-id-tagged logs — for operators.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict

LOGGER_NAME = "numera"
logger = logging.getLogger(LOGGER_NAME)

#: Extra fields lifted from log records into the structured payload, when present.
_EXTRA_FIELDS = ("correlation_id", "agent_id", "event_type", "subject_id")


class Metrics:
    """Thread-naive, in-process counters + latency samples. Replace with a real backend later."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._latencies: dict[str, list[float]] = defaultdict(list)

    def incr(self, name: str, n: int = 1) -> None:
        self._counters[name] += n

    def observe(self, name: str, value_ms: float) -> None:
        self._latencies[name].append(value_ms)

    def snapshot(self) -> dict[str, object]:
        latency = {
            name: {
                "count": len(samples),
                "avg_ms": round(sum(samples) / len(samples), 3),
                "max_ms": round(max(samples), 3),
            }
            for name, samples in self._latencies.items() if samples
        }
        return {"counters": dict(self._counters), "latency": latency}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    """Attach a single JSON stream handler to the ``numera`` logger (idempotent)."""
    if any(getattr(h, "_numera", False) for h in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    handler._numera = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
