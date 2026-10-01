"""Registration failures the delivery layer has to tell apart.

An error class lives in ``api`` when a caller's *response* depends on which one
it is. :class:`AppNameTakenError` is that: it is the difference between "retry
with another name" (409) and "something broke" (500), and the web adapter cannot
learn it any other way — the layer rules forbid adapters importing
``community.core``, where the error is raised.

The rest of the app domain's errors stay in ``core``. ``PrefixTakenError`` and
``PrefixAllocationError`` are handled entirely inside the registrar's retry loop
and never reach a response as themselves, so promoting them here would widen the
contract without giving anyone anything to act on.
"""

from __future__ import annotations


class AppNameTakenError(RuntimeError):
    """``(app_name, env)`` is already claimed by another application.

    Never retryable by the code that hit it: the name came from the caller, so
    the same insert fails identically every time until they pick a different
    one. Carries both halves of the key, because "billing is taken" is not
    actionable without knowing which environment it is taken in.
    """

    def __init__(self, app_name: str, env: str) -> None:
        super().__init__(f"app_name {app_name!r} is already used in env {env!r}")
        self.app_name = app_name
        self.env = env
