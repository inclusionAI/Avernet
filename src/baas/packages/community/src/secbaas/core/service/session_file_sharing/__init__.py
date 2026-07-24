"""Session File Sharing — dispatcher implementation.

TODO(77-02): DefaultSessionFileSharingDispatcher cannot be imported in the
packages tree until the Phase 75 session_file_ticket repository is mirrored
from the flat tree (``secbaas.community.core.repository.session_file_ticket``)
to the packages tree (``secbaas.core.repository.session_file_ticket``).

The SessionTicketRepository is a hard dependency of the dispatcher.  Once
the repository is available in the packages tree, add:

    from ._dispatcher import DefaultSessionFileSharingDispatcher

and copy the flat tree dispatcher implementation (Plan 77-01 Task 2 Part C)
with all imports adjusted from ``secbaas.community.*`` to ``secbaas.*``.
"""