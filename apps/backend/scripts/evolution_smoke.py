#!/usr/bin/env python3
"""Non-production 01–06 acceptance bootstrap for a baselined disposable Bot.

Build the deployment injector once in this standalone process; never import the
HTTP app or create an extra application injector inside a running Backend.
Experience defaults to a fixture; --session-id reads a real Engine snapshot.
Candidate records are retained, but nothing is applied or promoted. See core/evolution_run/README.md for preconditions and limitations.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from agentclaw.community.core.bot_genome.models import BotRef
from agentclaw.community.di.evolution_smoke import run_smoke


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--confirm-test-bot", required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument(
        "--session-id", help="Explicit real Engine session for 02 snapshot acceptance"
    )
    parser.add_argument(
        "--include-draft-experience",
        action="store_true",
        help="Allow draft-only test Bot sessions; never relabel as service",
    )
    parser.add_argument("--worker-image", help="Digest-pinned acceptance worker image")
    parser.add_argument("--sandbox-kube-api")
    parser.add_argument("--sandbox-namespace")
    parser.add_argument("--sandbox-token-file", type=Path)
    parser.add_argument("--sandbox-ca-file", type=Path)
    parser.add_argument("--job-bind-ip", help="Routable local Pod IP, not loopback")
    args = parser.parse_args()
    sandbox_values = (args.worker_image, args.sandbox_kube_api, args.sandbox_namespace,
                      args.sandbox_token_file, args.sandbox_ca_file, args.job_bind_ip)
    if any(sandbox_values) and not all(sandbox_values):
        parser.error("sandbox mode requires all six explicit worker/Kubernetes settings")
    if args.include_draft_experience and not args.session_id:
        parser.error("--include-draft-experience requires --session-id")
    if args.confirm_test_bot != args.bot_id:
        parser.error("--confirm-test-bot must match --bot-id (disposable Bot only)")
    from agentclaw.community.di.profile import DeployProfile

    profile = DeployProfile.detect()
    if profile not in (
        DeployProfile.TEST,
        DeployProfile.SINGLEBOX,
        DeployProfile.LOCAL_K8S,
    ):
        parser.error("smoke is restricted to test/singlebox/local-k8s profiles")
    # Resolve ownership and engine facts through the real repository, not CLI guesses.
    from agentclaw.community.core.repository.protocols.bot import BotRepository
    from agentclaw.community.di.container import build_injector

    from agentclaw.community.di.config_bootstrap import register_config_provider
    from agentclaw.community.di.modules_bootstrap import register_corp_modules

    from agentclaw.community.adapters.http.boot import install_openclaw_config_path

    install_openclaw_config_path()
    register_config_provider(profile)
    register_corp_modules(profile)
    injector = build_injector(profile=profile)
    row = injector.get(BotRepository).get_by_id_and_owner(args.bot_id, args.owner_id)
    if row is None:
        parser.error("test Bot does not exist or does not belong to this owner")
    bot = BotRef(
        entity_id=row["entity_id"],
        bot_id=args.bot_id,
        owner_id=args.owner_id,
        engine_type=row["active_engine"],
        bot_type=row["bot_type"],
        entity_type=row["entity_type"],
    )

    # Reject an unbaselined Bot before persisting any transcript-derived data.
    injector.get(GenomeRegistryProtocol).get_revision(bot, "active")

    async def execute_with(sandbox_config):
        if args.session_id is None:
            return await run_smoke(
                registry=injector.get(GenomeRegistryProtocol),
                bot=bot,
                workdir=args.workdir,
                sandbox_config=sandbox_config,
            )
        from agentclaw.community.api.engine_runtime_service import (
            EngineRuntimeRelayProtocol,
        )
        from agentclaw.community.core.engine_runtime.session_key import (
            SessionKeyCodecRegistry,
        )
        from agentclaw.community.plugins.evolution.session_experience import (
            SessionSnapshotExperience,
        )

        # Keep transcript-derived data out of the checkout. run_smoke owns its fresh subdir.
        args.workdir.mkdir(parents=True, exist_ok=False, mode=0o700)
        experience = SessionSnapshotExperience(
            bot=bot,
            database=args.workdir / "experience.db",
            include_draft=args.include_draft_experience,
        )
        try:
            episode = await experience.ingest(
                relay=injector.get(EngineRuntimeRelayProtocol),
                codecs=injector.get(SessionKeyCodecRegistry),
                session_id=args.session_id,
                runtime_stage="draft" if args.include_draft_experience else "online",
            )
            return await run_smoke(
                registry=injector.get(GenomeRegistryProtocol),
                bot=bot,
                workdir=args.workdir / "run",
                experience_factory=experience,
                expected_episode_ids=(episode,),
                sandbox_config=sandbox_config,
            )
        finally:
            experience.close()

    async def execute():
        if args.worker_image is None:
            return await execute_with(None)
        import ssl
        import httpx
        from agentclaw.community.di.evolution_smoke_sandbox import SmokeSandbox
        from agentclaw.community.plugins.evolution.kubernetes_worker import (
            KubernetesJobWorker, SMOKE_COMMAND,
        )
        if not args.sandbox_kube_api.startswith("https://"):
            raise ValueError("Kubernetes API must use verified TLS")
        if args.sandbox_token_file.stat().st_mode & 0o077:
            raise ValueError("controller token file must not be group/world readable")
        tls = ssl.create_default_context(cafile=str(args.sandbox_ca_file))
        async with httpx.AsyncClient(
            base_url=args.sandbox_kube_api, verify=tls, trust_env=False, timeout=10,
            headers={"Authorization": "Bearer " + args.sandbox_token_file.read_text().strip()},
        ) as client:
            adapter = KubernetesJobWorker(client, namespace=args.sandbox_namespace)
            return await execute_with(SmokeSandbox(adapter, args.worker_image,
                                                   args.job_bind_ip, SMOKE_COMMAND))

    report = asyncio.run(execute())
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
