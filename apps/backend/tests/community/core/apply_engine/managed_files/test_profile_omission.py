"""A Teclaw artifact includes the platform's full current identity state."""
from dataclasses import replace

import pytest

from agentclaw.community.core.apply_engine.managed_files.ports import PlatformIdentity
from agentclaw.community.core.apply_engine.managed_files.reader import ManagedFilesComposeReader
from agentclaw.community.core.config_compose.models import ComposeOccasion, ComposeRequest
from .test_store_ports import _store, _identity, _run, _apply, _ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [None, [], [{"type": "AGENTS.md", "content": "new rules"}]])
async def test_identity_category_replacement_is_reflected_in_complete_snapshot(identity):
    from datetime import datetime, timezone
    import json
    from agentclaw.community.core.apply_engine.capabilities import Category
    from agentclaw.community.core.apply_engine.materialisers.identity import IdentityMaterialiser
    from agentclaw.community.core.apply_engine.orchestrator import ApplyOrchestrator
    from agentclaw.community.core.apply_engine.order import ApplyStep
    from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
    from agentclaw.community.core.bot_config_manifest.binding import bound_manifest_state, manifest_digest
    from tests.community.core.bot_config_manifest.compiler.test_compile import _compiler

    store, objects = _store()
    port = PlatformIdentity(store)
    await port.update_bot_file("staff", "u_owner", "b_1", "IDENTITY.md", "platform profile", "u_owner")
    manifest = {} if identity is None else {"identity": identity}
    document = json.dumps({"schema_version": 1, "manifest": manifest})
    compiler, content = _compiler(document)
    ctx = _ctx()
    compiled = await compiler.compile(ctx, bound_manifest_state(manifest_digest(document)))
    orchestrator = ApplyOrchestrator({Category.IDENTITY: IdentityMaterialiser(port, content)}, steps=lambda _: (ApplyStep(Category.IDENTITY, 1),))
    report = await orchestrator.apply(ctx, compiled.state, apply_id="identity_put", trigger="explicit", started_at=datetime.now(timezone.utc))
    assert report.status is ApplyStatus.SUCCEEDED
    reader = ManagedFilesComposeReader(store=store, declared_state_provider=lambda: None)
    request = ComposeRequest(entity_type="staff", entity_id="u_owner", bot_id="b_1", user_id="u_owner", engine_type="teclaw", occasion=ComposeOccasion.MANIFEST_APPLY)
    refs = {ref.name: ref for ref in reader.identity_files(request)}
    expected = {"IDENTITY.md"} if identity is None else ({"AGENTS.md"} if identity else set())
    assert set(refs) == expected
    if identity is None:
        assert objects.objects[f"teclaw/dev/bolt_data/{refs['IDENTITY.md'].path}"] == b"platform profile"


@pytest.mark.parametrize("profile", ["", "explicit profile"])
def test_explicit_profile_is_present_in_apply_and_provision_snapshots(profile):
    store, objects = _store()
    _run(_apply(_identity(PlatformIdentity(store)), _ctx(), [{"type": "IDENTITY.md", "content": profile}]))
    reader = ManagedFilesComposeReader(store=store, declared_state_provider=lambda: None)
    request = ComposeRequest(entity_type="staff", entity_id="u_owner", bot_id="b_1", user_id="u_owner", engine_type="teclaw", occasion=ComposeOccasion.MANIFEST_APPLY)
    refs = reader.identity_files(request)
    if profile:
        assert len(refs) == 1 and refs[0].name == "IDENTITY.md"
        assert objects.objects[f"teclaw/dev/bolt_data/{refs[0].path}"] == profile.encode()
    else:
        assert refs == []
    assert reader.identity_files(replace(request, occasion=ComposeOccasion.PROVISION)) == refs
    assert reader.platform_owns(replace(request, occasion=ComposeOccasion.RUNTIME)) is False
