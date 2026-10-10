from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import logging
import pytest

from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityPermissionError,
)
from agentclaw.community.core.caller_identity.credential import CallerCredentialError
from agentclaw.community.core.caller_identity.session_authorizer import (
    CallerSessionAuthorizer,
)


def _authorizer(provider="teclaw", items=None):
    bindings = MagicMock()
    bindings.get_by_id.return_value = SimpleNamespace(device_provider=provider)
    resolver = MagicMock()
    resolver.resolve_for_binding.return_value = SimpleNamespace(
        conn_info={"binding_id": 9}
    )
    transport = MagicMock()
    transport.invoke = AsyncMock(return_value={"success": True, "data": items or []})
    authorizer = CallerSessionAuthorizer(
        binding_repository=bindings,
        resolver=resolver,
        transport=transport,
        runtime_bindings=MagicMock(),
    )
    return authorizer, resolver, transport


def _request(key="private-session"):
    return dict(
        bot_id="bot-1",
        owner_id="owner-1",
        caller_user_id="caller-1",
        binding_id=9,
        stage="draft",
        session_key=key,
    )


@pytest.mark.parametrize("key", [None, "", " \t"])
def test_teclaw_missing_key_rejects_before_network(key):
    authorizer, resolver, transport = _authorizer()
    with pytest.raises(CallerCredentialError):
        authorizer.authorize(**_request(key))
    resolver.resolve_for_binding.assert_not_called()
    transport.invoke.assert_not_called()


def test_exact_runtime_user_session_authorized_with_safe_log(caplog):
    authorizer, resolver, transport = _authorizer(
        items=[{"id": "private-session", "user_id": "caller-1", "agent_id": "bot-1"}]
    )
    with caplog.at_level(logging.INFO):
        authorizer.authorize(**_request())
    resolver.resolve_for_binding.assert_called_once_with(9, "caller-1", bot_id="bot-1")
    assert (
        transport.invoke.call_args.kwargs["params"]["session_key"] == "private-session"
    )
    assert "caller_session_lookup_succeeded" in caplog.text
    assert "private-session" not in caplog.text


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"id": "other", "user_id": "caller-1"}],
        [{"id": "private-session", "user_id": "other"}],
        [{"id": "private-session", "user_id": "caller-1", "agent_id": "other-bot"}],
    ],
)
def test_missing_or_foreign_session_denied(items):
    authorizer, _, _ = _authorizer(items=items)
    with pytest.raises(CallerIdentityPermissionError):
        authorizer.authorize(**_request())


def test_non_teclaw_has_no_new_io():
    authorizer, resolver, transport = _authorizer(provider="arca")
    authorizer.authorize(**_request(None))
    resolver.resolve_for_binding.assert_not_called()
    transport.invoke.assert_not_called()


def test_session_transport_failure_hides_response_and_chain(caplog):
    authorizer, _, transport = _authorizer()
    transport.invoke.side_effect = ValueError("private-session secret-token")
    with caplog.at_level(logging.INFO), pytest.raises(CallerCredentialError) as error:
        authorizer.authorize(**_request())
    assert error.value.__suppress_context__
    assert "caller_session_lookup_failed" in caplog.text
    assert "private-session" not in caplog.text
    assert "secret-token" not in caplog.text


def test_enveloped_session_list_checks_all_ownership_fields():
    authorizer, _, transport = _authorizer()
    transport.invoke.return_value = {
        "success": True,
        "data": {
            "items": [
                {"id": "private-session", "user_id": "caller-1", "agent_id": "bot-1"}
            ]
        },
    }
    assert authorizer.authorize(**_request()) is True


def test_session_list_explicit_failure_is_not_permission():
    authorizer, _, transport = _authorizer()
    transport.invoke.return_value = {"success": False, "data": []}
    with pytest.raises(CallerCredentialError):
        authorizer.authorize(**_request())


def test_foreign_session_title_cannot_leak_session_id(caplog):
    authorizer, _, transport = _authorizer()
    transport.invoke.return_value = {
        "success": True,
        "data": [
            {
                "id": "foreign-sensitive-session",
                "title": "foreign-sensitive-session",
                "user_id": "other",
                "agent_id": "bot-1",
            }
        ],
    }
    with caplog.at_level(logging.INFO), pytest.raises(CallerIdentityPermissionError):
        authorizer.authorize(**_request())
    assert "foreign-sensitive-session" not in caplog.text


def test_session_failed_status_is_logged_without_response_credentials(caplog):
    from agentclaw.community.plugin_api.device_adapter_transport import (
        DeviceAdapterHTTPStatusError,
    )

    authorizer, _, transport = _authorizer()
    transport.invoke.side_effect = DeviceAdapterHTTPStatusError(503, "private-session")
    with caplog.at_level(logging.INFO), pytest.raises(CallerCredentialError):
        authorizer.authorize(**_request())
    assert "status_code=503" in caplog.text
    assert "private-session" not in caplog.text


def test_session_failed_json_preserves_business_error_and_hides_token(caplog):
    from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterHTTPStatusError
    authorizer, _, transport = _authorizer()
    transport.invoke.side_effect = DeviceAdapterHTTPStatusError(503, '{"business":"unavailable","token":"sensitive-error-token"}')
    with caplog.at_level(logging.INFO), pytest.raises(CallerCredentialError):
        authorizer.authorize(**_request())
    assert "unavailable" in caplog.text
    assert "sensitive-error-token" not in caplog.text
