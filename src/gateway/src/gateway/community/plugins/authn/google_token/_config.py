"""Google userinfo endpoint + timeout, used by ``GoogleUserStrategy``.

Mirrors the relevant subset of BCS ``bcs-auth-google/src/config.rs``. Only the
userinfo endpoint and a bounded timeout are used today (the strategy verifies a
presented access token by calling Google's userinfo, like BCS
``get_user_info``).
"""

from __future__ import annotations

# Google userinfo endpoint — called by the ``google`` strategy with a bearer
# access token to resolve the caller's identity (mirrors BCS ``get_user_info``).
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
# Per-request timeout for the userinfo call, so a hung Google endpoint cannot
# pin the gateway request indefinitely (mirrors BCS's bounded reqwest client).
USERINFO_TIMEOUT_SECONDS = 10.0
