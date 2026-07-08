from ._protocols import SecretStorePlugin

# Dev/test SM4 key — this is a non-production key used for local development
# and testing.  Production deployments resolve the SM4 key from the secret
# store instead.
DEV_SM4_KEY = "rzq4b5aJpS62/FMDfK18Bw=="

__all__ = [
    "DEV_SM4_KEY",
    "SecretStorePlugin",
]
