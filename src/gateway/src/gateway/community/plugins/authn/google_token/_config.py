"""Google OAuth endpoint constants.

Mirrors ``bcs/crates/plugins/bcs-auth-google/src/config.rs``. The gateway's
``google`` strategy only consumes :data:`GOOGLE_USERINFO_URL` — it verifies a
presented Google access token by calling the userinfo endpoint, exactly like
BCS's ``get_user_info``. The authorization/token URLs and scopes are kept for
parity with BCS and for any future login-flow port.
"""

from __future__ import annotations

# Google authorization endpoint (parity with BCS; unused by the gateway today).
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
# Google token endpoint (parity with BCS; unused by the gateway today).
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Google userinfo endpoint — called by the ``google`` strategy with a bearer
# access token to resolve the caller's identity (mirrors BCS ``get_user_info``).
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
# OAuth scopes requested during a Google login (parity with BCS).
GOOGLE_SCOPES = "openid profile email"
# Per-request timeout for the userinfo call, so a hung Google endpoint cannot
# pin the gateway request indefinitely (mirrors BCS's bounded reqwest client).
USERINFO_TIMEOUT_SECONDS = 10.0
