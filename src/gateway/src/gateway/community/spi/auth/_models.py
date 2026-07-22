"""Auth SPI — shared data models.

Implementation-agnostic ``AuthenticatedUser`` used as the return type for
``AuthPlugin.get_login_user()``.
"""

from pydantic import BaseModel


class AuthenticatedUser(BaseModel):
    """Neutral, provider-agnostic identity of an authenticated end user.

    Returned by ``AuthPlugin.get_login_user()``. Each auth backend
    (enterprise SSO, community OIDC, local) maps its own user representation
    onto these fields. Only ``id`` and ``username`` are guaranteed; the rest
    are optional profile attributes a provider may not supply.
    """

    id: str                          # stable, provider-issued unique user id
    username: str                    # account / login name
    display_name: str | None = None  # human-facing name, if the provider supplies one
    full_name: str | None = None     # full / legal name, if available
    email: str | None = None         # contact email, if available
    phone: str | None = None         # contact phone, if available
    tenant_id: str | None = None     # tenant this identity belongs to, if the provider scopes it
