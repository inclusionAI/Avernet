"""Expert reconciliation inside the existing GitSync lifecycle and lock."""

from __future__ import annotations

import subprocess
from dataclasses import asdict

from agentclaw.community.core.expert_market.models import ExpertScope
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


def sync_git_experts(service, source_commit: str | None = None) -> dict:
    if service._expert_scanner is None:
        return {"skipped": True}
    scope = ExpertScope(
        get_current_avernet_tenant(), get_current_env(), service._expert_source_repo_id
    )
    if source_commit is None:
        result = subprocess.run(
            ["git", "rev-parse", "FETCH_HEAD^{commit}"],
            cwd=service.config.local_bare_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        source_commit = result.stdout.strip()
    return asdict(
        service._expert_scanner.scan_git(
            service.config.local_bare_repo, source_commit=source_commit, scope=scope
        )
    )


def sync_local_experts(service) -> dict:
    """Local seeds are a real Git revision, never a made-up commit marker."""
    if service._expert_scanner is None:
        return {"skipped": True}
    # Standard local setup uses aiworkbench/ as the Git root. Reading Git
    # objects preserves same-commit shared references and verifies provenance.
    root = service.config.agents_target.parent
    result = subprocess.run(
        ["git", "rev-parse", "HEAD^{commit}"], cwd=root, capture_output=True, text=True
    )
    if result.returncode:
        return {"skipped": True, "reason": "local expert seed has no Git provenance"}
    scope = ExpertScope(
        get_current_avernet_tenant(), get_current_env(), service._expert_source_repo_id
    )
    return asdict(
        service._expert_scanner.scan_git(
            root, source_commit=result.stdout.strip(), scope=scope
        )
    )
