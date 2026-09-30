"""Type-safe encryptable header rules for Arca outbound operations.

Provides EncryptableHeaderRule class extending project-owned HeaderOperationRule
with an encrypt_value flag for secure header value storage.
"""

from pydantic import BaseModel, ConfigDict, Field

from ._outbound_rule import HeaderOperationRule


class EncryptableHeaderRule(HeaderOperationRule):  # type: ignore[misc]
    """Header operation rule with optional encryption support.

    Extends project-owned HeaderOperationRule (isomorphic to arca SDK) to add encrypt_value flag.
    This provides type-safe API for callers to specify encrypted storage.

    Fields:
        - All fields from HeaderOperationRule (domains, action, header_name, value, etc.)
        - encrypt_value: bool = False - Flag indicating whether value should be encrypted for storage

    Usage Example:
        EncryptableHeaderRule(
            domains=["*.api.example.com"],
            action="SET_HEADER",
            header_name="Authorization",
            value="Bearer secret-token",
            encrypt_value=True,  # Service layer will encrypt before storage
        )
    """

    model_config = ConfigDict(extra="allow")

    encrypt_value: bool = Field(default=False, description="value是否应加密存储")


class EncryptableOutBoundRule(BaseModel):
    """Outbound operation rule with encryptable header rules.

    Alternative to project-owned OutBoundOperationRule with type-safe
    encrypt_value flag support in header_operation_rules.

    Fields:
        header_operation_rules: List of EncryptableHeaderRule

    Usage Example:
        EncryptableOutBoundRule(
            header_operation_rules=[
                EncryptableHeaderRule(..., encrypt_value=True),
            ]
        )
    """

    model_config = ConfigDict(extra="allow")

    header_operation_rules: list[EncryptableHeaderRule] = Field(
        default_factory=list, description="Encryptable header operation rules list"
    )
