"""Domain errors.

Every error carries a stable machine-readable ``code`` (TRD §8.3 error model). Adapters map
these to transport-specific responses (HTTP status codes, MCP tool errors) without the domain
knowing about transports.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain errors.

    Attributes:
        code: Stable, machine-readable error code (e.g. ``"QUOTE_EXPIRED"``).
        message: Human-readable explanation.
        details: Optional structured context for the caller.
        recoverable: Whether retrying after a change could succeed.
    """

    code: str = "DOMAIN_ERROR"
    recoverable: bool = False

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class UnknownCurrency(DomainError):
    code = "UNKNOWN_CURRENCY"
    recoverable = True


class CurrencyMismatch(DomainError):
    code = "CURRENCY_MISMATCH"


class PairNotAllowed(DomainError):
    code = "PAIR_NOT_ALLOWED"
    recoverable = True


class InvalidValueDate(DomainError):
    code = "INVALID_VALUE_DATE"
    recoverable = True


class InvalidExposure(DomainError):
    """Raised when a declared exposure is malformed (e.g. non-positive amount, same currency)."""

    code = "INVALID_EXPOSURE"
    recoverable = True


class InstrumentNotSupported(DomainError):
    """Raised when an instrument is recognised but not yet implemented in this build phase."""

    code = "INSTRUMENT_NOT_SUPPORTED"
    recoverable = False


class QuoteExpired(DomainError):
    code = "QUOTE_EXPIRED"
    recoverable = True


class IdempotencyConflict(DomainError):
    code = "IDEMPOTENCY_CONFLICT"


class VenueUnavailable(DomainError):
    code = "VENUE_UNAVAILABLE"
    recoverable = True


class RateUnavailable(DomainError):
    code = "RATE_UNAVAILABLE"
    recoverable = True


class NotFound(DomainError):
    code = "NOT_FOUND"
    recoverable = False


class PolicyLimitExceeded(DomainError):
    code = "POLICY_LIMIT_EXCEEDED"
    recoverable = True


class InstrumentNotAllowed(DomainError):
    code = "INSTRUMENT_NOT_ALLOWED"
    recoverable = True


class InvalidState(DomainError):
    """Raised when an operation is not valid for an entity's current state (e.g. approving a
    non-parked order)."""

    code = "INVALID_STATE"
    recoverable = False


class AttributionImbalance(DomainError):
    """Raised when cost components do not reconcile to the all-in cost (invariant I2)."""

    code = "ATTRIBUTION_IMBALANCE"
    recoverable = False
