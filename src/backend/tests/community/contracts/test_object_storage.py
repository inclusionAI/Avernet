"""Rule 25 conformance — ObjectStoragePlugin.

Consumer under test: ``SkillPublishService._upload_zip_file``
(core/skill_center/services/skill_publish_service.py:446). It reads a
ZIP, calls ``ObjectStoragePlugin.put_object`` and ``.sign_url``, and
returns the signed URL. The local impl ``MockObjectStoragePlugin`` is
MagicMock-backed, so we can both observe the calls and assert the
returned URL is the one the mock produced.

Plugin-hit assertion: ``put_object`` and ``sign_url`` mocks must record
the call. A consumer bypassing the plugin would never trigger them.
"""
from __future__ import annotations

from agentclaw.community.core.skill_center.services.skill_publish_service import SkillPublishService
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin


def test_upload_zip_routes_through_oss_storage_plugin(world, tmp_path) -> None:
    zip_path = tmp_path / "skill.zip"
    zip_path.write_bytes(b"PK\x03\x04 fake-zip-bytes")

    svc = world.get(SkillPublishService)
    skill = {"name": "demo_skill", "version": "1.0.0"}

    signed = svc._upload_zip_file(skill, str(zip_path))

    oss = world.get(ObjectStoragePlugin)
    # Plugin-hit assertions on the MagicMock-backed local impl.
    oss.put_object.assert_called_once()
    oss.sign_url.assert_called_once()
    # The signed URL the consumer returned came from the plugin's mock.
    assert signed.startswith("mock://")


def test_community_column_binds_contract_shaped_object_storage(community_world) -> None:
    # The default community impl is the filesystem store, which writes under a
    # data root — running the writing consumer against the shared community_world
    # would touch the filesystem, so the real put/sign behavior is covered by the
    # plugin unit tests (temp roots). Here we pin that the community column wires
    # a contract-shaped impl: the default backend, structurally an
    # ObjectStoragePlugin.
    from agentclaw.community.plugins.community.object_storage import (
        CommunityFsObjectStorage,
    )

    oss = community_world.get(ObjectStoragePlugin)
    assert isinstance(oss, CommunityFsObjectStorage)
