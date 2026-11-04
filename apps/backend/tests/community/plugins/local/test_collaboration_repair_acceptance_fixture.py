"""The opt-in acceptance source preserves real delivery and lazy credentials."""
import json
from unittest.mock import Mock

from agentclaw.community.di.config import BcnConfig
from agentclaw.community.di.modules.collaboration_repair_module import (
    HttpCollaborationRepairPlugins,
)
from tests.community.acceptance.collaboration_repair.fixture_module import (
    LiveRepairPlugins,
)


def test_credentials_are_loaded_lazily_without_changing_default_config(tmp_path):
    path = tmp_path / "provider.json"
    config = BcnConfig(base_url="http://127.0.0.1:21000")
    source = LiveRepairPlugins(path, config, Mock(), Mock(), timeout_seconds=30)
    assert not source.available
    path.write_text(json.dumps({
        "provider_id": "fixture-provider",
        "provider_admin_token": "test-token",
    }))
    assert source.available
    resolved = source._resolve()
    assert isinstance(resolved, HttpCollaborationRepairPlugins)
    assert resolved.bcn_service._config.provider_registration_enabled is True
    assert resolved.bcn_service._config.provider_id == "fixture-provider"
    assert config.provider_id == ""
    assert config.provider_admin_token == ""
    assert config.provider_registration_enabled is False
    assert resolved.bcn_service._http is source._http_client
    assert resolved.mint_principal is source._mint_principal


def test_bootstrap_adds_repair_source_after_owner_seam(monkeypatch):
    import runpy
    from pathlib import Path
    import agentclaw.community.di as di
    import agentclaw.community.di.container as container
    from agentclaw.community.di.profile import DeployProfile
    from tests.community.acceptance.collaboration_repair.fixture_module import (
        CollaborationRepairFixtureModule,
    )

    original = Mock()
    monkeypatch.setattr(di, "build_injector", original)
    monkeypatch.setattr(container, "build_injector", original)
    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF", "1")
    runtime = Path(__file__).parents[2] / "acceptance/bot_ownership_sync/runtime/sitecustomize.py"
    runpy.run_path(str(runtime))
    di.build_injector(profile=DeployProfile.SINGLEBOX)
    assert isinstance(original.call_args.kwargs["extra_modules"][-1], CollaborationRepairFixtureModule)
