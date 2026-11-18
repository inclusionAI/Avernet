"""Arca recycle transport retained at the existing host composition seam."""

from __future__ import annotations


class ArcaRecycleTransport:
    """Provider primitives injected by composition, like ArcaRestartTransport.

    Missing capabilities fail closed; this object never selects another provider.
    """

    def __init__(self, *, destroy=None, is_destroyed=None):
        self.destroy, self.is_destroyed = destroy, is_destroyed

    def submit(self, target, request_id):
        if self.destroy is None or self.destroy(target["sandbox_id"]) is not True:
            raise RuntimeError("Arca recycle release unconfirmed")
        return {"request_id": request_id}

    def observe(self, target):
        if self.is_destroyed is None:
            return "UNKNOWN"
        return (
            "RELEASED" if self.is_destroyed(target["sandbox_id"]) is True else "UNKNOWN"
        )
