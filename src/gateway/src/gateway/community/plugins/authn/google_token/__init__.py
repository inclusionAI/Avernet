"""``google`` auth strategy plugin — verify a Google access token via userinfo.

The :class:`GoogleUserStrategy` calls Google's userinfo endpoint itself
(mirroring BCS ``bcs-auth-google`` ``get_user_info``) and maps the response onto
a ``UserPrincipal``; the user-info logic lives in the strategy, not a separate
provider.
"""

from ._strategy import GoogleUserStrategy

__all__ = [
    "GoogleUserStrategy",
]
