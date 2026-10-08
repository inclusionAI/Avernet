"""Schemas for the org group — directory-identity lookups by `?user_id=`."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OrgUserIdentity(BaseModel):
    """The identity+department of the user named by `?user_id=`, from the staff
    directory.

    A directory lookup, not a whoami: the response is the user whose work
    number was passed, resolved off the staff directory (the gateway signs only
    the caller, so another user's identity is read off HR, not the principal).
    The identity and department attributes are optional because absence is a
    real state — the directory may supply no value (or no record at all), and
    they are null rather than invented when absent. `tenant` is the caller's,
    scoped off the verified principal.
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
        description="The work number this lookup was for — the `?user_id=` "
        "value the caller passed to name whose identity+department to return."
    )
    username: str | None = Field(
        description="The login name, when the staff directory supplied one; "
        "null otherwise (e.g. the reader is unwired or the user has no record)."
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
