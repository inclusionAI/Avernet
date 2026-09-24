import base64

from ._protocols import SecretStorePlugin

# Dev/test SM4 key — this is a non-production key used for local development
# and testing.  Production deployments resolve the SM4 key from the secret
# store instead.
DEV_SM4_KEY = base64.b64encode(b"\x00" * 16).decode("ascii")

__all__ = [
    "DEV_SM4_KEY",
    "SecretStorePlugin",
]
