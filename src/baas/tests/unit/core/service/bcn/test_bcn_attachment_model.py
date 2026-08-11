"""Tests for Attachment Pydantic model — parsing, validation, and to_domain() conversion.

Covers:
- Attachment.to_domain() full-field conversion (all 8 fields)
- Optional fields defaulting to None when absent
- ChatSendRequest.model_validate parsing attachments array from JSON
- ChatSendRequest.attachments None when key is absent
- ChatInjectRequest.model_validate parsing attachments array from JSON
"""

from secbaas.community.adapters.web.routers.bcn_downlink.bcn_model import (
    Attachment as PydanticAttachment,
)
from secbaas.community.adapters.web.routers.bcn_downlink.bcn_model import (
    ChatInjectRequest,
    ChatSendRequest,
)
from secbaas.community.api.bcn import (
    Attachment as DomainAttachment,
)

# ── helpers ──


def _make_attachment_dict(attachment_id="att_1", **overrides) -> dict:
    """Build a dict for Pydantic Attachment.model_validate with sensible defaults."""
    return {
        "attachment_id": attachment_id,
        "type": "image",
        "file_name": "test.png",
        "url": f"https://cdn.example.com/{attachment_id}",
        **overrides,
    }


def _make_send_request_body(**overrides) -> dict:
    """Build a minimal valid ChatSendRequest JSON dict."""
    return {
        "type": "req",
        "id": "run-1",
        "session_id": "session-1",
        "bcn_group_id": "group-1",
        "to_bot": {
            "provider_id": "baas",
            "provider_bot_ref": "bot-1",
        },
        "from": {"kind": "human", "id": "user-1"},
        "message": {"role": "user", "content": "hello"},
        **overrides,
    }


# ── Attachment to_domain() tests ──


def test_attachment_to_domain_all_fields():
    """to_domain() maps all 8 fields correctly from Pydantic to domain dataclass."""
    att = PydanticAttachment.model_validate(
        _make_attachment_dict(
            mime_type="image/png",
            size=1024,
            sha256="abc123def",
            expires_at=1700000000,
        )
    )
    domain = att.to_domain()

    assert isinstance(domain, DomainAttachment), (
        "to_domain() must return DomainAttachment"
    )
    assert domain.attachment_id == "att_1"
    assert domain.type == "image"
    assert domain.file_name == "test.png"
    assert domain.mime_type == "image/png"
    assert domain.size == 1024
    assert domain.sha256 == "abc123def"
    assert domain.url == "https://cdn.example.com/att_1"
    assert domain.expires_at == 1700000000


def test_attachment_to_domain_optional_fields_none():
    """Optional fields (mime_type, size, sha256, expires_at) default to None in to_domain()."""
    att = PydanticAttachment.model_validate(
        _make_attachment_dict()  # only required fields
    )
    domain = att.to_domain()

    assert isinstance(domain, DomainAttachment), (
        "to_domain() must return DomainAttachment"
    )
    assert domain.attachment_id == "att_1"
    assert domain.type == "image"
    assert domain.file_name == "test.png"
    assert domain.url == "https://cdn.example.com/att_1"
    assert domain.mime_type is None
    assert domain.size is None
    assert domain.sha256 is None
    assert domain.expires_at is None


# ── ChatSendRequest attachments tests ──


def test_chat_send_request_parses_attachments():
    """ChatSendRequest.model_validate parses an 'attachments' JSON array correctly."""
    body = _make_send_request_body(
        attachments=[
            _make_attachment_dict(attachment_id="att_1", file_name="photo.png"),
            _make_attachment_dict(attachment_id="att_2", file_name="diagram.png"),
        ],
    )
    req = ChatSendRequest.model_validate(body)

    assert req.attachments is not None, "attachments must not be None when provided"
    assert len(req.attachments) == 2
    assert req.attachments[0].attachment_id == "att_1"
    assert req.attachments[0].file_name == "photo.png"
    assert req.attachments[0].type == "image"
    assert req.attachments[1].attachment_id == "att_2"
    assert req.attachments[1].file_name == "diagram.png"


def test_chat_send_request_without_attachments():
    """ChatSendRequest.attachments is None when the key is absent from JSON."""
    body = _make_send_request_body()  # no 'attachments' key
    req = ChatSendRequest.model_validate(body)

    assert req.attachments is None, "attachments must be None when key is absent"


# ── ChatInjectRequest attachments test ──


def test_chat_inject_request_parses_attachments():
    """ChatInjectRequest.model_validate parses an 'attachments' JSON array correctly."""
    body = {
        "type": "req",
        "id": "inject-1",
        "session_id": "session-1",
        "bcn_group_id": "group-1",
        "to_bot": {
            "provider_id": "baas",
            "provider_bot_ref": "bot-1",
        },
        "from": {"kind": "human", "id": "user-1"},
        "message": {"role": "user", "content": "injected"},
        "attachments": [
            _make_attachment_dict(attachment_id="att_1"),
        ],
    }
    req = ChatInjectRequest.model_validate(body)

    assert req.attachments is not None, "attachments must not be None when provided"
    assert len(req.attachments) == 1
    assert req.attachments[0].attachment_id == "att_1"
