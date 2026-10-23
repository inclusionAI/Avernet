"""Compile a revision into the apply engine's ``DesiredState`` — the genome's compiler.

The mapping is ``api-contracts/genome/v1/manifest-v1-compatibility.md``: every
stored file becomes a ``StoredContent`` by its digest, a local skill its
``acm-tree-v1`` tree, a Skill Center skill its pinned version, a
shared-repository skill its path, MCP entries their typed override, and the
script's stored body is read back. A revision is total (contract §4.3), so
every category it covers is *declared* — an empty list declares "none", which
the engine converges to by removing what is there — and the two it never
covers (``cli_tools``, ``engine_config``) are left undeclared, so an apply
leaves them as they are. ``memory`` is not delivered until RSI-05.

``GenomeCompiler`` is the ``DesiredStateCompiler`` for ``GenomeStateRef``: a
promotion id resolves to the promotion row, whose revision compiles. The row
is ``applying`` while the delivery seam answers and ``applied`` as soon as it
has — before the apply run that delivers it compiles in its worker — so both
resolve; only a promotion whose delivery never started (``apply_failed``) has
nothing to deliver. It is registered in the run service's compiler map;
RSI-04 is its first caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Collection

from agentclaw.community.core.apply_engine.compiler import Compiled, GenomeStateRef
from agentclaw.community.core.apply_engine.constructs import ApplyConstruct
from agentclaw.community.core.apply_engine.desired_state import (
    CenterSkillVersion,
    Declaration,
    DesiredIdentityFile,
    DesiredMcpServer,
    DesiredResource,
    DesiredResourceFile,
    DesiredResourceTree,
    DesiredScript,
    DesiredSkill,
    DesiredState,
    LocalSkillPackage,
    McpOverride,
    RepoSkillPath,
    StoredContent,
)
from agentclaw.community.core.bot_genome.errors import NotAppliable, Reason, RevisionNotFound
from agentclaw.community.core.bot_genome.models import BotRef, ContentReader
from agentclaw.community.core.bot_genome.spec_models import (
    CenterSkill,
    GenomeSpec,
    McpServerEntry,
    RepoSkill,
    ResourceEntry,
    SkillEntry,
)
from agentclaw.community.core.repository.protocols.bot.genome import GenomePromotionRepositoryProtocol
from agentclaw.community.utils.env_utils import get_current_env

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentclaw.community.core.apply_engine.context import ApplyContext
    from agentclaw.community.core.bot_genome.genome_registry_protocol import GenomeRegistryProtocol

__all__ = ["GenomeCompiler", "compile_spec"]

#: The promotion status whose delivery never started: there is nothing to deliver for it.
_NEVER_STARTED = "apply_failed"


def _skill_source(entry: SkillEntry) -> LocalSkillPackage | CenterSkillVersion | RepoSkillPath:
    if isinstance(entry, CenterSkill):
        return CenterSkillVersion(entry.center_version)
    if isinstance(entry, RepoSkill):
        return RepoSkillPath(entry.repo_path)
    return LocalSkillPackage(StoredContent(entry.digest))


def _resource(entry: ResourceEntry) -> DesiredResource:
    if entry.path.endswith("/"):
        return DesiredResourceTree(entry.path, StoredContent(entry.digest))
    return DesiredResourceFile(entry.path, StoredContent(entry.digest))


def _mcp(entry: McpServerEntry) -> DesiredMcpServer:
    override = McpOverride.from_config(entry.config.to_json()) if entry.config is not None else None
    return DesiredMcpServer(entry.server_code, override)


def compile_spec(spec: GenomeSpec, content: ContentReader) -> DesiredState:
    """The desired state of ``spec``; ``content`` supplies the script body.

    Pure: nothing is fetched and nothing is filed, because a revision only
    ever names bytes the bot can already reach. A revision the state model
    refuses — one recorded before a rule the model now enforces, such as a
    resource outside the resources root — is ``NotAppliable`` naming the
    rule, not a compiler error.
    """
    script: Declaration[DesiredScript]
    if spec.script is not None:
        script = Declaration((DesiredScript(content.read(spec.script.digest).decode("utf-8")),))
    else:
        script = Declaration(())
    try:
        return DesiredState(
            script=script,
            identity=Declaration(tuple(DesiredIdentityFile(e.type, StoredContent(e.digest)) for e in spec.persona)),
            resources=Declaration(tuple(_resource(e) for e in spec.resources)),
            skills=Declaration(tuple(DesiredSkill(e.name, _skill_source(e)) for e in spec.skills)),
            mcp=Declaration(tuple(_mcp(e) for e in spec.tools.mcp)),
        )
    except ValueError as exc:
        raise NotAppliable([Reason("not_appliable", "spec", f"the revision cannot be delivered as recorded: {exc}")]) from exc


class GenomeCompiler:
    """Compiles ``GenomeStateRef``: the revision the named promotion delivers."""

    def __init__(
        self,
        *,
        registry: GenomeRegistryProtocol,
        promotions: GenomePromotionRepositoryProtocol,
        env: Callable[[], str] = get_current_env,
    ) -> None:
        self._registry = registry
        self._promotions = promotions
        self._env = env

    def _revision_of(self, ctx: ApplyContext, ref: GenomeStateRef) -> str:
        """The revision id the promotion delivers; a missing promotion, or one whose delivery never started, is refused."""
        row = self._promotions.get(
            env=self._env(), entity_id=ctx.entity_id, bot_id=ctx.bot_id, promotion_id=ref.promotion_id
        )
        if row is None:
            raise RevisionNotFound(f"promotion {ref.promotion_id}")
        if row.status == _NEVER_STARTED:
            raise NotAppliable(
                [
                    Reason(
                        "not_appliable",
                        "promotion",
                        f"promotion {ref.promotion_id} is {row.status}: its delivery never started, so there is "
                        "nothing to deliver",
                    )
                ]
            )
        return row.revision_id

    def check(self, ctx: ApplyContext, ref: GenomeStateRef) -> None:
        self._revision_of(ctx, ref)

    async def compile(
        self, ctx: ApplyContext, ref: GenomeStateRef, *, only: Collection[ApplyConstruct] | None = None
    ) -> Compiled:
        revision = self._revision_of(ctx, ref)
        bot = BotRef(
            entity_id=ctx.entity_id,
            bot_id=ctx.bot_id,
            owner_id=ctx.owner_id,
            engine_type=ctx.engine_type,
            bot_type=ctx.bot_type,
        )
        state = self._registry.compile(bot, revision)
        # A creation half applies its own constructs only; the rest stay undeclared.
        return Compiled(state if only is None else state.only(only))
