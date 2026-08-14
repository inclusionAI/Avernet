"""JWT verification utility — re-exported for adapter consumption.

The implementation lives in core.utils.secret_utils, but is re-exported here
so that adapter layers can use it without importing from core (which is banned
by architecture layer rules).
"""

from __future__ import annotations

from typing import Any

import jwt


def verify_jwt_token(
    token: str, secret_key: str
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """Verify a JWT token (compatible with standard HS256 JWTs).

    Args:
        token: JWT token string
        secret_key: signing secret key

    Returns:
        tuple of (is_valid, error_message, payload_dict)
    """
    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        return True, None, payload

    except jwt.ExpiredSignatureError:
        return False, "Token expired", None
    except jwt.InvalidTokenError as e:
        return False, f"Token invalid: {e}", None
    except Exception as e:
        return False, f"Token verification failed: {e}", None
