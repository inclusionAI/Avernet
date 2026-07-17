"""Factory for file transfer ticket repository."""

from ._protocol import TicketRepository


def get_ticket_repository() -> TicketRepository:
    from secbaas.community.bootstrap import get_container

    return get_container().repository.ticket_repository()
