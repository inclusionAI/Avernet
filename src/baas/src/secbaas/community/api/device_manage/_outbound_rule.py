"""Outbound operation rule models (project-owned, isomorphic to Arca SDK types).

These models replace Arca SDK types in public interfaces.
Only ArcaPaasService converts back to SDK types at the boundary.
"""

from pydantic import BaseModel, ConfigDict


class HeaderOperationRule(BaseModel):
    """Header operation rule for outbound traffic control."""

    model_config = ConfigDict(extra="allow")

    domains: list[str]
    action: str
    header_name: str
    value: str
    placeholder: str | None = None
    separator: str | None = None


class OutBoundOperationRule(BaseModel):
    """Outbound operation rule containing header operation rules."""

    model_config = ConfigDict(extra="allow")

    header_operation_rules: list[HeaderOperationRule] | None = None
