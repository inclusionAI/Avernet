"""App domain — canonical data-access (``AppRegistry`` SPI impl) + registration.

Holds the ORM row (:class:`AppRow`), the canonical :class:`AppRepository` impl,
:class:`AppRegistrar` (registers + persists apps), and :class:`APIKeyGenerator`
(the app credential scheme). The
:class:`~gateway.community.spi.app.AppRegistry` contract lives in the app SPI.
The authn ``app_token`` strategy depends on the SPI, not this module.

``_key_gen.py`` is a **verbatim copy** of
``src/baas/src/secbaas/community/core/service/api_gateway/_key_gen.py`` and must
stay byte-identical to it: secbaas's existing API-key records are migrated into
``avernet_application``, and the stored hash records only its salt, so the
digest, iteration count, and encoding are implicit constants shared by both
sides. Edit the scheme in both files or in neither — a one-sided change
invalidates every migrated key.

``tests/unit/plugins/test_app_key_gen.py`` fails on a one-sided change *when it
runs*, which covers edits to this module. It does not cover the other direction:
the gateway CI job is selected by changed paths under ``src/gateway``, so a
commit touching only secbaas's copy skips this suite entirely. Until that gap is
closed, a secbaas-side edit to the scheme must be paired with a gateway-side
re-copy by hand.
"""

from ._key_gen import APIKeyGenerator
from ._orm import AppRow
from ._registrar import AppRegistrar, IssuedApp, PrefixAllocationError
from ._repository import AppRepository, PrefixTakenError

__all__ = [
    "APIKeyGenerator",
    "AppRegistrar",
    "AppRepository",
    "AppRow",
    "IssuedApp",
    "PrefixAllocationError",
    "PrefixTakenError",
]
