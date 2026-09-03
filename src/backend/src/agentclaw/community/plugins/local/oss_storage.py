"""MockObjectStoragePlugin — test implementation of ObjectStoragePlugin.

Each Protocol method is backed by a ``unittest.mock.MagicMock`` so
tests can:

- read defaults: a fresh instance returns ``True`` for write ops and a
  predictable ``mock://...`` URL for ``sign_url``;
- override behaviour per-test: assign ``return_value`` / ``side_effect``
  on the method (e.g. ``mock.put_object.return_value = False``) or
  raise from a ``side_effect``;
- assert calls: read ``.call_args`` / ``.mock_calls`` like any other
  ``MagicMock`` (e.g. ``mock.put_object.assert_called_once_with(...)``);
- reset between tests: ``reset_all_mocks()`` restores defaults and
  clears call history.

Bound as a singleton by ``TestingSkillCenterModule``. The singleton is
intentional — tests reach into the injector to grab the same instance
the production-shaped service constructors received.
"""
from __future__ import annotations

from unittest.mock import MagicMock
from agentclaw.community.plugin_api.object_storage import (
    ObjectCopyCapability,
    ObjectCreateResult,
    ObjectReadResult,
    ObjectReadStatus,
    ImmutableObjectStorageCapability,
    ObjectStoragePlugin,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


def _default_sign_url(key: str, expires: int = 7200) -> str:
    """Default ``sign_url`` behaviour for the mock — a stable ``mock://`` URL.

    Exposed as a module-level function so ``reset_all_mocks`` and
    ``__init__`` share the exact same callable shape.
    """
    return f"mock://{key}?expires={expires}"


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.MOCK,
    rationale="MagicMock-backed responses",
)
class MockObjectStoragePlugin(
    MockSeam, ObjectStoragePlugin, ImmutableObjectStorageCapability, ObjectCopyCapability
):
    """Reconfigurable mock satisfying :class:`ObjectStoragePlugin`."""

    def __init__(self) -> None:
        self.put_object: MagicMock = MagicMock(return_value=True)
        self.create_object_if_absent: MagicMock = MagicMock(
            return_value=ObjectCreateResult.CREATED
        )
        self.put_file: MagicMock = MagicMock(return_value=True)
        self.get_object: MagicMock = MagicMock(return_value=None)
        self.read_object: MagicMock = MagicMock(
            return_value=ObjectReadResult(ObjectReadStatus.NOT_FOUND)
        )
        self.copy_object: MagicMock = MagicMock(return_value=True)
        self.delete_object: MagicMock = MagicMock(return_value=True)
        self.list_objects: MagicMock = MagicMock(return_value=[])
        self.sign_url: MagicMock = MagicMock(side_effect=_default_sign_url)
        self.ensure_directory: MagicMock = MagicMock(return_value=True)
        self.get_etag: MagicMock = MagicMock(return_value=None)
        self.set_object_acl: MagicMock = MagicMock(return_value=True)

    def reset_all_mocks(self) -> None:
        """Restore default return values / side_effects and clear call history.

        Note: ``MagicMock.reset_mock(return_value=True)`` does **not** set
        ``return_value`` to ``True`` — that kwarg means "also reset the
        ``return_value`` child mock". The explicit assignments below are
        what actually restore the defaults.
        """
        self.put_object.reset_mock()
        self.put_object.return_value = True
        self.put_object.side_effect = None

        self.create_object_if_absent.reset_mock()
        self.create_object_if_absent.return_value = ObjectCreateResult.CREATED
        self.create_object_if_absent.side_effect = None

        self.put_file.reset_mock()
        self.put_file.return_value = True
        self.put_file.side_effect = None

        self.get_object.reset_mock()
        self.get_object.return_value = None
        self.get_object.side_effect = None

        self.read_object.reset_mock()
        self.read_object.return_value = ObjectReadResult(ObjectReadStatus.NOT_FOUND)
        self.read_object.side_effect = None

        self.copy_object.reset_mock()
        self.copy_object.return_value = True
        self.copy_object.side_effect = None

        self.delete_object.reset_mock()
        self.delete_object.return_value = True
        self.delete_object.side_effect = None

        self.list_objects.reset_mock()
        self.list_objects.return_value = []
        self.list_objects.side_effect = None

        self.sign_url.reset_mock()
        self.sign_url.side_effect = _default_sign_url

        self.ensure_directory.reset_mock()
        self.ensure_directory.return_value = True
        self.ensure_directory.side_effect = None

        self.get_etag.reset_mock()
        self.get_etag.return_value = None
        self.get_etag.side_effect = None

        self.set_object_acl.reset_mock()
        self.set_object_acl.return_value = True
        self.set_object_acl.side_effect = None
