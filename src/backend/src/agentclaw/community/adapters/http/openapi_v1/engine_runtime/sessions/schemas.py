"""Request/response models for the sessions group."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.contracts import Page
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    MessageRole,
)


# Why this exists, kept out of the docstring because a published model's
# docstring becomes the caller-facing schema description:
#
# The two paged routes in this group read from a bot's own device. It reports no
# count for either collection and offers no way to obtain one short of reading
# every record — prohibitively expensive for a session list, where each record
# costs an extra round trip to enrich. Rather than report a number that is
# wrong, or one that quietly caps at some prefix length, `total` is documented
# for what it is. Every other list endpoint on this API reads a database we own
# and reports an exact `Page.total`; this subclass exists so the two are not
# conflated by a client that trusts the shared description.
class BoundedPage[T](Page[T]):
    """A page of items whose total is a lower bound, not an exact count."""

    total: int = Field(
        description="Lower bound on the number of items matching the query — at "
        "least this many exist. Becomes the exact count once you reach the last "
        "page (identifiable by a page shorter than `page_size`). Paginate until "
        "a short page rather than computing a page count from this value."
    )


class Message(BaseModel):
    """One message in a session."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message_id": "msg-1",
                "session_id": "session:2d20edc1:user:165137",
                "role": "assistant",
                "content": "Done — the report is in reports/q3.md.",
                "gmt_create": "2026-07-30T09:12:04+00:00",
            }
        }
    )

    message_id: str = Field(description="Identifier of this message.")
    session_id: str = Field(description="Session this message belongs to.")
    role: MessageRole = Field(description="Who or what produced the message.")
    content: str = Field(description="Message body.")
    gmt_create: str = Field(
        description="When the message was created (ISO 8601); empty if the "
        "engine did not report a timestamp."
    )


class Session(BaseModel):
    """A conversation session on the bot's device."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "session:2d20edc1:user:165137",
                "title": "Quarterly report",
                "agent_id": "main",
                "model": "openai/gpt-5.3",
                "permission_mode": "on-miss",
                "cwd": "/workspace",
                "runtime": "",
                "message_count": 12,
                "gmt_create": "2026-07-30T09:00:00+00:00",
                "gmt_modified": "2026-07-30T09:12:04+00:00",
            }
        }
    )

    session_id: str = Field(
        description="Session identifier. Use this value verbatim in the path of "
        "the per-session endpoints — do not re-encode it."
    )
    title: str = Field(description="Human-readable session title.")
    agent_id: str = Field(description="Agent the session belongs to; may be empty.")
    model: str = Field(description="Model the session is using; may be empty.")
    # Typed `str`, not ApprovalMode: the engine's read path has no closed set.
    permission_mode: str = Field(
        description="Approval mode in force for this session; empty if unset."
    )
    cwd: str = Field(description="Working directory on the device; may be empty.")
    runtime: str = Field(description="Runtime label reported by the bot; may be empty.")
    # Lower bound, not exact: one bundled engine reports an authoritative
    # messageCount, the other derives it from a history fetch capped at 100,
    # so a longer session saturates at the cap. Lifting that would cost an
    # extra RPC per session on a list route — the same trade rejected for
    # Page.total, and resolved the same way: bound it and say so.
    message_count: int = Field(
        description="Number of messages in the session, as a lower bound. "
        "Depending on the bot's engine this may saturate at an internal fetch "
        "cap instead of the session's true length, so read a large value as "
        "'at least this many'. The messages endpoint reports an exact total "
        "once its last page is reached."
    )
    gmt_create: str = Field(description="Creation time (ISO 8601); may be empty.")
    gmt_modified: str = Field(description="Last-modified time (ISO 8601); may be empty.")


class SessionCreate(BaseModel):
    """Create-a-session request body. Both fields are optional, but the body itself
    is required — send `{}` to create a session with the bot's defaults."""

    # No user_id / engine fields: the caller is the authenticated principal and
    # the engine is the bot's active one. extra="forbid" turns an attempt to
    # supply either into a 422 rather than a silent drop.
    #
    # The example is what makes the body discoverable. Every field being optional
    # does NOT make the body optional — FastAPI still requires it — so a caller
    # reading a schema with no example had nothing to send and got
    # `{'loc': ('body',), 'type': 'missing'}` back. The docstring says so and the
    # example shows one.

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"title": "Quarterly report", "model": "openai/gpt-5.3"}
        },
    )

    # Only fields every engine on this surface actually persists — the same
    # ruling `cwd` got on SessionUpdate, for the same reason. An agent was
    # offered here and withdrawn: `claude_code` encodes it into the session key
    # (`agent:{agent_id}:session:…`) so later reads recover it, while `openclaw`
    # builds `session:{uuid}:user:{user_id}` and never sends the agent at all,
    # then synthesises a 201 echoing the value it dropped. The association would
    # exist or not depending on the bot's engine, and the response would claim
    # it either way. `extra="forbid"` makes a caller still sending `agent_id` a
    # 422 rather than a false 201. Reading and filtering by agent are
    # unaffected: `Session.agent_id` is still published, and `GET …/sessions`
    # still takes an `agent_id` filter, which the engine applies upstream.
    title: str | None = Field(default=None, description="Optional session title.")
    model: str | None = Field(
        default=None, description="Optional model for the session."
    )


class SessionUpdate(BaseModel):
    """Partial update. Omitted fields are left unchanged."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"title": "Quarterly report (final)"}},
    )

    # Only fields every engine on this surface actually applies. A working
    # directory was offered here and withdrawn: one of the two bundled engines
    # applies it and the other discards it without saying so, which would have
    # made the same request succeed and do nothing depending on which engine the
    # bot runs. `extra="forbid"` means a caller still sending `cwd` gets a 422
    # rather than a silent no-op.
    title: str | None = Field(default=None, description="New session title.")
    model: str | None = Field(default=None, description="New model.")


# Named concretisations rather than `BoundedPage[Session]` used inline. Pydantic
# builds a parametrised generic with no `__doc__`, so the schema description —
# the only place the lower-bound caveat is stated — would silently vanish from
# the published document. Naming them also gives generated clients `SessionPage`
# instead of a mangled generic name.
class SessionPage(BoundedPage[Session]):
    """A page of sessions whose total is a lower bound, not an exact count."""


class MessagePage(BoundedPage[Message]):
    """A page of messages, newest page first.

    Page 1 holds the most recent messages, page 2 the ones before those, and so
    on back through the history. Messages stay in chronological order within a
    page; it is the pages that run newest-first.

    As with any bounded page, total is a lower bound while full pages keep
    coming and becomes exact once you reach a page shorter than page_size.
    """


__all__ = [
    "BoundedPage",
    "Message",
    "MessagePage",
    "Session",
    "SessionCreate",
    "SessionPage",
    "SessionUpdate",
]
