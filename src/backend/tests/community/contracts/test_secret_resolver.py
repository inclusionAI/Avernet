"""Rule 25 conformance — SecretResolver.

Consumer under test: ``SkillScanService._initialize_sdk``
(core/skill_center/services/skill_scan.py:115). It calls
``secret_resolver.get_secret(...)`` and treats ``None`` as the signal
to use the in-code fallback credentials. The local
``LocalSecretResolver`` always returns ``None``.

Plugin-hit assertion: instantiating ``SkillScanService`` is heavy
(SDK init, env probing), so we verify the contract through the
DI-bound resolver — the same instance the service receives at
construction time. The consumer's fallback branch is unreachable
without a real ``None`` from the plugin.
"""
from __future__ import annotations

from agentclaw.community.plugin_api.secret_resolver import SecretResolver


def test_local_secret_resolver_returns_none_for_any_secret(world) -> None:
    resolver = world.get(SecretResolver)
    # The local impl's contract: every secret unavailable → None.
    # Consumers (SkillScanService) read this as "use fallback".
    assert resolver.get_secret("any_secret_name") is None
    assert resolver.get_secret("other_manual_agentclaw_skillscan_agent_api_key") is None


def test_community_secret_resolver_returns_none_for_unset_secret(
    community_world, monkeypatch
) -> None:
    # The community impl satisfies the same consumer contract: an unset secret
    # resolves to None, so SkillScanService takes its fallback branch. Clear the
    # backing env vars so the "unset" precondition is hermetic (no ambient leak).
    name = "OTHER_MANUAL_AGENTCLAW_SKILLSCAN_AGENT_API_KEY"
    monkeypatch.delenv(f"AGENTCLAW_SECRET_{name}_VALUE", raising=False)
    monkeypatch.delenv(f"AGENTCLAW_SECRET_{name}_USER", raising=False)
    resolver = community_world.get(SecretResolver)
    assert resolver.get_secret("other_manual_agentclaw_skillscan_agent_api_key") is None
