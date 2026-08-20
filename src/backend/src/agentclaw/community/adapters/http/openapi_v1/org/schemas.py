"""Schemas for the org group — the verified caller's own identity."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OrgUserIdentity(BaseModel):
    """The end user the caller's credential names, as resolved at the gateway.

    Every field is read off the verified principal — nothing is taken from the
    request, so the answer cannot be steered. The display attributes are
    optional because absence is a real state of the identity contract: an
    identity provider may not supply them, and they are null rather than
    invented when absent. The department attributes are optional the same way:
    they are null until the identity provider supplies them.
    """

    # The docstring above is published verbatim as the schema's description
    # (see test_schema_docs), so it carries no RST markup and no internal
    # names; rationale that needs either lives here instead.
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "u-8f3a2c",
                "username": "alice@example.com",
                "display_name": "Alice",
                "full_name": "Alice Zhang",
                "tenant": "acme-tenant",
                "dept_no": "D-1001",
                "dept_name": "Platform Engineering",
                "dept_path": "Ant Group/Platform",
            }
        }
    )

    user_id: str = Field(
        description="The caller's end-user id — the exact value to pass as the "
        "`user_id` query parameter on every user-scoped operation of this API."
    )
    username: str = Field(
        description="The login name the identity provider resolved for the "
        "caller."
    )
    display_name: str | None = Field(
        description="Short display name, when the identity provider supplied "
        "one; null otherwise."
    )
    full_name: str | None = Field(
        description="Full name, when the identity provider supplied one; null "
        "otherwise."
    )
    tenant: str = Field(
        description="The tenant this caller's requests are scoped to."
    )
    dept_no: str | None = Field(
        description="The caller's department number, when the identity provider "
        "supplied one; null otherwise."
    )
    dept_name: str | None = Field(
        description="The caller's department name, when the identity provider "
        "supplied one; null otherwise."
    )
    dept_path: str | None = Field(
        description="The caller's department lineage path, when the identity "
        "provider supplied one; null otherwise."
    )
