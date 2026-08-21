"""collaboration bots public-API group — new-version publish-to-users.

Served by the backend (the botpublish approval flow + the public_scope
callback live here); the gateway pulls ``/openapi/v1/collaboration/bots/...``
out of the collaboration→bcs namespace onto the backend. Auth (grant /
admission) is deferred for now: the handler identifies the caller but applies
no bot-grant check.
"""
from .router import router

__all__ = ["router"]
