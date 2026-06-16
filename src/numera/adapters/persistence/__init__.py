"""SQLAlchemy/SQLite persistence adapters implementing the repository ports.

The build defaults to in-memory repositories; selecting ``NUMERA_PERSISTENCE=sqlite`` swaps in this
durable implementation with **no change above the repository ports** (NFR-7). Aggregates are stored
as JSON documents keyed by their identifiers — a pragmatic v1; a normalized schema with Alembic
migrations is the production step (ADR-008).
"""
