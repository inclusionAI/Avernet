"""Local Skill lifecycle across real Backend storage and Engine HTTP file I/O.

Run via claude_code_file_lifecycle.sh, which provides both distributions. Only
control-plane repositories/permissions and device transport are test doubles;
package storage, HTTP routing, the file plugin, and runtime mapping are real.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytest_plugins = ["tests.community.conftest"]
import tests.community.conftest  # noqa: F401 — bootstrap before either distribution

from agentclaw.community.core.devices.services.baas_device_filesystem import (
    BaasDeviceFileSystem,
)
from agentclaw.community.core.skill_center.factories import LocalSkillPackageStorage
from agentclaw.community.core.skill_center.errors import LocalSkillStorageError
from tests.community.core.skill_center.test_local_skill_upload_service import (
    _Bot,
    _ConcurrentRepo,
    _Factory,
    _service,
    _skill_md,
    _zip,
)
from engine.community.api.file.router import router
from engine.community.engines.claude_code.engine import ClaudeCodeCommunityEngine
from engine.community.manager import EngineManager
from engine.community.plugins.claude_code.layout_pool import (
    publish_claude_code_pool_mappings,
    verify_claude_code_pool_mappings,
    MappingSourceLayout,
    SkillMapping,
)


class _HttpTransport:
    def __init__(self, client):
        self.client = client

    def post(self, path, *, json):
        return self.client.post(path, json=json)

    def post_multipart(self, path, *, files, data):
        return self.client.post(path, files=files, data=data)


class _RealStorageFactory(_Factory):
    def local_skill_package_storage(self, *, name, directory_name=None, **kwargs):
        directory = str(self.local_dir / (directory_name or name))
        return directory, LocalSkillPackageStorage(self._filesystem, directory)

    def local_skill_package_storage_for_locator(self, *, locator, **kwargs):
        return LocalSkillPackageStorage(self._filesystem, locator)


@pytest.mark.asyncio
async def test_create_activate_replace_and_failed_replace_restore_nested_bytes(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CODE_DEFAULT_CWD", str(home / ".claude_code/workspace"))
    engine = ClaudeCodeCommunityEngine()
    manager = EngineManager("claude_code")
    manager._active_engine = engine
    monkeypatch.setattr(EngineManager, "_instance", manager)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        fs = BaasDeviceFileSystem(
            transport=_HttpTransport(client), conn_info={}, path_mapper=lambda p: p
        )
        factory = _RealStorageFactory(fs)
        factory.local_dir = home / ".claude_code/workspace/skills/skills-local"
        repo = _ConcurrentRepo()
        service = _service(
            fs, bot=_Bot(engine="claude_code"), repo=repo, factory=factory
        )
        first = {
            "SKILL.md": _skill_md(),
            "scripts/nested/run.py": b"old script",
            "assets/image.bin": b"\xff\x00old\x80",
        }
        result = await service.upload_local_skill(
            bot_id="bot", owner_id="owner", actor_id="owner", package=_zip(first)
        )
        assert result["skill"]["active"] is False
        source = factory.local_dir / "upload-skill"
        target = home / ".claude/skills/upload-skill"
        assert not target.exists()
        # The image startup creates the empty discovery root, not active skills.
        target.parent.mkdir(parents=True)
        mapping = SkillMapping(source=str(source), target=str(target))
        published = publish_claude_code_pool_mappings(
            mappings=[mapping], source_layout=MappingSourceLayout.LEGACY, home=home
        )
        assert published.published, published
        assert target.is_symlink() and target.resolve() == source.resolve()
        assert (target / "SKILL.md").read_bytes() == first["SKILL.md"]
        verified = verify_claude_code_pool_mappings(
            mappings=[mapping], source_layout=MappingSourceLayout.LEGACY, home=home
        )
        assert verified.valid, verified
        second = {
            **first,
            "scripts/nested/run.py": b"new script",
            "assets/image.bin": b"\x00\xfeNEW",
        }
        replaced = await service.upload_local_skill(
            bot_id="bot", owner_id="owner", actor_id="owner", package=_zip(second)
        )
        assert replaced["operation"] == "updated"
        for path, content in second.items():
            assert (target / path).read_bytes() == content
        # Fail the metadata switch after canonical bytes were replaced. The
        # existing compensator must restore the complete on-disk old package.
        repo.replace_bot_local_skill = lambda **kwargs: None
        with pytest.raises(LocalSkillStorageError):
            await service.upload_local_skill(
                bot_id="bot",
                owner_id="owner",
                actor_id="owner",
                package=_zip({**first, "SKILL.md": _skill_md(description="failed")}),
            )
        for path, content in second.items():
            assert (target / path).read_bytes() == content
        assert {p.name for p in factory.local_dir.iterdir()} == {"upload-skill"}
