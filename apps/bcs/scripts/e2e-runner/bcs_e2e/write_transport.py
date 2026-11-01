"""Allowlisted write transport for the pilot; no arbitrary URL/method escape hatch.

WriteClient is composed from stateless mixin planes (registration, friend, core)
so the flat module stays far under the 1,000-line budget; the planes import the
leaf write_transport_error module instead of this base, which breaks the cycle.
"""
from __future__ import annotations

from urllib.request import HTTPRedirectHandler, ProxyHandler, HTTPSHandler, build_opener

from .profile import browser_headers, origin
from .transport import tls_context
from .write_transport_core import WriteCoreTransportMixin
from .write_transport_error import WriteTransportError
from .write_transport_friend import WriteFriendTransportMixin
from .write_transport_registration import WriteRegistrationTransportMixin


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WriteClient(WriteRegistrationTransportMixin, WriteFriendTransportMixin, WriteCoreTransportMixin):
    def __init__(self, profile: dict, *, local_auth=None):
        self.profile = profile
        self.base = origin(profile["entrypoint"]["origin"], profile["environment"]["tier"])
        self.local_auth = local_auth
        owned_local = profile["auth"] == {"kind": "owned-local"}
        if owned_local or local_auth is not None:
            from .local_auth import require_local_auth
            require_local_auth(profile, local_auth)
        self.headers = browser_headers(profile)
        for header, ref in profile["auth"].get("header_env", {}).items():
            value = __import__("os").environ.get(ref, "")
            if not value or not value.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise WriteTransportError("missing or invalid authentication reference")
            self.headers[header] = value
        if profile["auth"].get("authorization_scheme") == "bearer" and "Authorization" in self.headers:
            token = self.headers["Authorization"]
            if token and not token.startswith("Bearer "):
                self.headers["Authorization"] = "Bearer " + token
        self.opener = build_opener(ProxyHandler({}), NoRedirect()) if owned_local else build_opener(HTTPSHandler(context=tls_context()), NoRedirect())
