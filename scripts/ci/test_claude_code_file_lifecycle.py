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
from tests.community.contracts.test_local_skill_storage import (
    factory as make_factory,
)
from agentclaw.community.plugins.community.local_skill_storage import (
    EngineLocalSkillStorage,
)
from types import SimpleNamespace
from agentclaw.community.core.skill_center.errors import LocalSkillStorageError
from tests.community.core.skill_center.test_local_skill_upload_service import (
    _Bot,
    _ConcurrentRepo,
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


@pytest.mark.asyncio
async def test_create_activate_replace_and_failed_replace_restore_nested_bytes(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CODE_DEFAULT_CWD", str(home / ".openclaw/workspace"))
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
        local_dir = home / ".claude_code/workspace/skills/skills-local"
        monkeypatch.setattr(
            "agentclaw.community.plugins.community.local_skill_storage.pool_paths_for_engine",
            lambda engine: SimpleNamespace(legacy_local=str(local_dir)),
        )
        factory = make_factory(EngineLocalSkillStorage())
        factory._device_fs_dispatcher.for_bot.return_value = fs
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
        source = local_dir / "upload-skill"
        target = home / ".claude/skills/upload-skill"
        assert not target.exists()
        # Existing activation requires a discovery root; this test does not
        # claim to validate fresh-container initialization.
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
        assert {p.name for p in local_dir.iterdir()} == {"upload-skill"}

        # Cleanup the published active entry through the same HTTP file port.
        # Unlinking discovery must retain the package source and its bytes.
        removed = client.post("/api/file/remove", json={"target_path": str(target)})
        assert removed.status_code == 200, removed.text
        assert not target.is_symlink()
        assert (source / "SKILL.md").read_bytes() == second["SKILL.md"]
        assert (
            client.post(
                "/api/file/rmtree", json={"target_path": str(target.parent)}
            ).status_code
            == 403
        )
