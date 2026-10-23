"""``ManifestCompiler``: the bot's stored document → a ``DesiredState``.

The one ``DesiredStateCompiler`` for ``ManifestStateRef``. Per compile it
re-reads and re-validates the stored document (capabilities resolve from the
bot's engine, which can change after a document is accepted), opens a
``CompileSession`` (budget, named sources, git baselines), compiles each
declared construct through its entry compiler in apply order, and closes the
session before answering — every byte worth keeping is in the content store
by then, the checkout trees are not.

A bot with no document compiles to ``DesiredState.EMPTY``: it applies
nothing and reports nothing applied, the same rule that makes an absent
document read as empty rather than a 404.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from agentclaw.community.core.apply_engine.compiler import Compiled, ManifestStateRef
from agentclaw.community.core.apply_engine.constructs import ManifestCategory, ManifestSection
from agentclaw.community.core.apply_engine.desired_state import DesiredState
from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyReport, ApplyStatus
from agentclaw.community.core.bot_config_manifest.apply.source_resolver import DeclaredSourceResolver
from agentclaw.community.core.bot_config_manifest.apply.source_session import SourceSession
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    BotConfigManifestServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.compiler.baselines import (
    BASELINE_HISTORY_APPLIES,
    last_resolutions,
)
from agentclaw.community.core.bot_config_manifest.compiler.declared import declared_entries
from agentclaw.community.core.bot_config_manifest.compiler.entries.cli_tools import compile_cli_tools
from agentclaw.community.core.bot_config_manifest.compiler.entries.engine_config import compile_engine_config
from agentclaw.community.core.bot_config_manifest.compiler.entries.identity import compile_identity
from agentclaw.community.core.bot_config_manifest.compiler.entries.mcp import compile_mcp
from agentclaw.community.core.bot_config_manifest.compiler.entries.resources import compile_resources
from agentclaw.community.core.bot_config_manifest.compiler.entries.script import compile_script
from agentclaw.community.core.bot_config_manifest.compiler.entries.skills import compile_skills
from agentclaw.community.core.bot_config_manifest.compiler.session import CompileSession
from agentclaw.community.core.bot_config_manifest.fetch.git_source import GitSourceClient
from agentclaw.community.core.bot_config_manifest.services.apply_report_codec import report_from_payload
from agentclaw.community.core.content_store import ContentStoreProtocol
from agentclaw.community.core.repository.protocols.bot import BotConfigManifestApplyRepositoryProtocol
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator

__all__ = ["ManifestCompiler"]


class ManifestCompiler:
    """Compiles ``ManifestStateRef``: the bot's stored Manifest document."""

    def __init__(
        self,
        *,
        manifests: BotConfigManifestServiceProtocol,
        content: ContentStoreProtocol,
        resolver: DeclaredSourceResolver,
        validator: SkillPackageValidator,
        git_client_provider: Callable[[], GitSourceClient],
        applies: BotConfigManifestApplyRepositoryProtocol,
    ) -> None:
        self._manifests = manifests
        self._content = content
        self._resolver = resolver
        self._validator = validator
        self._git = git_client_provider
        self._applies = applies

    def check(self, ctx: ApplyContext, ref: ManifestStateRef) -> None:
        """Re-validate the stored document for the bot as it is now; raises ``ManifestValidationError``.

        Called for its exception: a document that cannot be applied is
        refused to the caller, before an apply id exists, exactly as it was
        when the run service validated here itself. Validity is relative to
        the bot, and the bot moves — the engine changed since the ``PUT``,
        or a construct's materialiser was withdrawn — so the ``PUT``'s own
        validation is not enough.
        """
        self._parsed_or_empty(ctx)

    async def compile(self, ctx: ApplyContext, ref: ManifestStateRef) -> Compiled:
        return await self.compile_document(ctx, self._parsed_or_empty(ctx))

    async def compile_document(self, ctx: ApplyContext, parsed: Mapping[str, Any]) -> Compiled:
        """Compile an already-parsed document (the stored one, or a caller's own, e.g. the creation preflight)."""
        session = CompileSession.open(
            ctx,
            source_session=SourceSession(
                sources=parsed.get("sources") or {},
                baselines=self._baselines(ctx),
                git=self._git(),
            ),
        )
        try:
            state = await self._state(session, parsed)
            sources = tuple(session.source_session.resolution_records())
        finally:
            session.close()
        return Compiled(state, sources)

    async def _state(self, session: CompileSession, parsed: Mapping[str, Any]) -> DesiredState:
        script = declared_entries(parsed, ManifestSection.SCRIPT)
        identity = declared_entries(parsed, ManifestCategory.IDENTITY)
        resources = declared_entries(parsed, ManifestCategory.RESOURCES)
        skills = declared_entries(parsed, ManifestCategory.SKILLS)
        mcp = declared_entries(parsed, ManifestCategory.MCP)
        cli_tools = declared_entries(parsed, ManifestCategory.CLI_TOOLS)
        manifest = parsed.get("manifest")
        engine_config_declared = isinstance(manifest, Mapping) and "engine_config" in manifest
        # Compiled in apply order, so fetches happen in the order the report lists.
        kwargs = dict(resolver=self._resolver, content=self._content)
        return DesiredState(
            script=compile_script(session, script) if script is not None else None,
            identity=await compile_identity(session, identity, **kwargs) if identity is not None else None,
            resources=await compile_resources(session, resources, **kwargs) if resources is not None else None,
            skills=(
                await compile_skills(session, skills, validator=self._validator, **kwargs)
                if skills is not None
                else None
            ),
            mcp=compile_mcp(mcp) if mcp is not None else None,
            engine_config=compile_engine_config(manifest.get("engine_config")) if engine_config_declared else None,
            cli_tools=await compile_cli_tools(session, cli_tools, **kwargs) if cli_tools is not None else None,
        )

    def _parsed_or_empty(self, ctx: ApplyContext) -> dict[str, Any]:
        """The stored document, re-validated, or ``{}`` when there is none."""
        record = self._manifests.get(entity_id=ctx.entity_id, bot_id=ctx.bot_id)
        if record is None:
            return {}
        return self._manifests.validate(
            document=record.document, active_engine=ctx.engine_type, bot_type=ctx.bot_type
        ).parsed

    def _baselines(self, ctx: ApplyContext) -> dict[tuple[str, str, str], str]:
        """Each git source's SHA as the bot's recent applies resolved it (W7)."""
        records = self._applies.recent(
            env=ctx.env, entity_id=ctx.entity_id, bot_id=ctx.bot_id, limit=BASELINE_HISTORY_APPLIES
        )
        reports: list[ApplyReport] = []
        for record in records:
            payload = json.loads(record.report) if record.report else {}
            reports.append(report_from_payload(payload, record=record, status=ApplyStatus(record.status)))
        return last_resolutions(reports)
