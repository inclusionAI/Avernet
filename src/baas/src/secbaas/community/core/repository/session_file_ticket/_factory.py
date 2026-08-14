"""Factory for session file ticket repository."""

from ._protocol import SessionTicketRepository


def create_session_ticket_repository(database) -> SessionTicketRepository:
    """Factory: create an OrmSessionTicketRepository wired to the given database."""
    # Lazy import to avoid circular dependency between factory and implementation.
    from ._orm_repository import OrmSessionTicketRepository

    return OrmSessionTicketRepository(database)
