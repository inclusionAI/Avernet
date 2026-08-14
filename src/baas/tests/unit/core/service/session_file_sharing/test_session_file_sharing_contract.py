"""Contract test: verify DefaultSessionFileSharingDispatcher satisfies SessionFileSharingDispatcher Protocol.

Per D-05: Contract test scope is Dispatcher layer only — verify that
DefaultSessionFileSharingDispatcher satisfies the @runtime_checkable
SessionFileSharingDispatcher Protocol (6 method signatures).

Per D-03: Co-located with Dispatcher unit tests at
tests/unit/core/service/session_file_sharing/.
"""

from unittest.mock import MagicMock

import pytest

from secbaas.community.api.session_file_sharing import (
    SessionFileSharingDispatcher,
)
from secbaas.community.core.service.session_file_sharing._dispatcher import (
    DefaultSessionFileSharingDispatcher,
)

# All six dispatch method names defined by the Protocol
DISPATCH_METHODS = [
    "dispatch_get_upload_url",
    "dispatch_complete_upload",
    "dispatch_cancel_upload",
    "dispatch_get_share_link",
    "dispatch_get_transfer_status",
    "dispatch_delete_transfer",
]


class TestSessionFileSharingContract:
    """Verify DefaultSessionFileSharingDispatcher satisfies the Protocol."""

    @pytest.fixture
    def dispatcher(self):
        return DefaultSessionFileSharingDispatcher(
            file_transfer_backend=MagicMock(),
            ticket_repo=MagicMock(),
        )

    def test_isinstance_satisfies_runtime_checkable_protocol(self, dispatcher):
        """DefaultSessionFileSharingDispatcher passes isinstance check against Protocol.

        SessionFileSharingDispatcher is decorated with @runtime_checkable,
        so isinstance() returns True when the object implements all
        Protocol methods.
        """
        assert isinstance(dispatcher, SessionFileSharingDispatcher), (
            "DefaultSessionFileSharingDispatcher must satisfy "
            "SessionFileSharingDispatcher Protocol"
        )

    @pytest.mark.parametrize("method_name", DISPATCH_METHODS)
    def test_has_dispatch_method(self, dispatcher, method_name):
        """All six dispatch methods exist on the dispatcher instance."""
        assert hasattr(dispatcher, method_name), (
            f"Missing protocol method: {method_name}"
        )
        method = getattr(dispatcher, method_name)
        assert callable(method), f"Protocol method {method_name} must be callable"

    def test_method_count(self, dispatcher):
        """Exactly six dispatch methods are present (no extras, no missing)."""
        method_names = [
            name
            for name in DISPATCH_METHODS
            if hasattr(dispatcher, name) and callable(getattr(dispatcher, name))
        ]
        assert len(method_names) == 6, (
            f"Expected 6 dispatch methods, found {len(method_names)}: {method_names}"
        )
