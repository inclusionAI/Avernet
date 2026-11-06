"""Known template aliases at the coding runtime environment boundary.

These aliases do not change the control-plane Bot type or template identity.
Unknown factory templates retain their own runtime value.
"""

LEGACY_BOT_TYPE_ENV_MAP = {
    "personalCoding": "personal",
    "applicationCoding": "application",
    "applicationCoding_oss": "application",
}
