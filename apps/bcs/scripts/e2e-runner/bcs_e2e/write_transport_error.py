"""Write-transport failure type kept in a leaf module so the mixin planes never import the base."""
from __future__ import annotations

from .correlation import RequestFailure


class WriteTransportError(RequestFailure):
    pass
