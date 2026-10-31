"""Compile a revision into the apply engine's ``DesiredState`` — the genome's compiler.

The mapping is ``api-contracts/genome/v1/manifest-v1-compatibility.md``: every
stored file becomes a ``ContentRef`` by its digest, a local skill its
``acm-tree-v1`` tree, a Skill Center skill its runtime name (the Center's published version applies), a
shared-repository skill its path, MCP entries their typed override, and the
script's stored body is read back. A revision is total (contract §4.3), so
every category it covers is *declared* — an empty list declares "none", which
the engine converges to by removing what is there — and the two it never
covers (``cli_tools``, ``engine_config``) are left undeclared, so an apply
leaves them as they are. ``memory`` is not delivered until RSI-05.

``GenomeCompiler`` is the ``DesiredStateCompiler`` for the ``genome`` kind (``genome_state(promotion_id)``): a
promotion id resolves to the promotion row, whose revision compiles. Compile
is one layer (revision → desired state), apply another (desired state → bot),
and this is only the first: it checks no promotion status. The compile is
pure — a revision is immutable and names only bytes already stored — so the
apply task compiling again on a re-run after a crash yields the same state;
the task queue never runs a finished or given-up task again, and the apply
lock keeps two promotions of one bot from overlapping. Only a promotion that
does not exist is refused. ``check``, the run service's synchronous gate under
the apply lock, also refuses a promotion whose ``from_revision`` is no longer
``active``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Collection

from agentclaw.community.core.apply_engine.compiler import Compiled, CompileRefused, DesiredStateCompiler, StateRef
from agentclaw.community.core.apply_engine.constructs import ApplyConstruct
from agentclaw.community.core.apply_engine.desired_state import (
    CenterSkillByName,
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
    ContentRef,
)
from agentclaw.community.core.bot_genome.errors import NotAppliable, Reason, RefConflict, RevisionNotFound
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
    from agentclaw.community.core.bot_genome.repository.models import GenomePromotionRow
    from agentclaw.community.core.bot_genome.genome_registry_protocol import GenomeRegistryProtocol

__all__ = ["GenomeCompiler", "compile_spec"]


def _skill_source(entry: SkillEntry) -> LocalSkillPackage | CenterSkillByName | RepoSkillPath:
    if isinstance(entry, CenterSkill):
        return CenterSkillByName()
    if isinstance(entry, RepoSkill):
        return RepoSkillPath(entry.repo_path)
    return LocalSkillPackage(ContentRef(entry.digest))


def _resource(entry: ResourceEntry) -> DesiredResource:
    if entry.path.endswith("/"):
        return DesiredResourceTree(entry.path, ContentRef(entry.digest))
    return DesiredResourceFile(entry.path, ContentRef(entry.digest))


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
    try:
        script: Declaration[DesiredScript]
        if spec.script is not None:
            script = Declaration((DesiredScript(content.read(spec.script.digest).decode("utf-8")),))
        else:
            script = Declaration(())
        return DesiredState(
            script=script,
            identity=Declaration(tuple(DesiredIdentityFile(e.type, ContentRef(e.digest)) for e in spec.persona)),
            resources=Declaration(tuple(_resource(e) for e in spec.resources)),
            skills=Declaration(tuple(DesiredSkill(e.name, _skill_source(e)) for e in spec.skills)),
            mcp=Declaration(tuple(_mcp(e) for e in spec.tools.mcp)),
        )
    except ValueError as exc:
        raise NotAppliable([Reason("not_appliable", "spec", f"the revision cannot be delivered as recorded: {exc}")]) from exc


#: The kind the genome registers its compiler under.
GENOME_STATE_KIND = "genome"


def genome_state(promotion_id: str) -> StateRef:
    """The state reference that applies a promotion's revision (the promotion row holds the revision id)."""
    if not promotion_id:
        raise ValueError("a genome state reference needs a promotion id")
    return StateRef(GENOME_STATE_KIND, promotion_id)


class GenomeCompiler(DesiredStateCompiler):
    """Compiles the ``genome`` kind: the revision the referenced promotion delivers."""

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

    def _revision_of(self, ctx: ApplyContext, ref: StateRef) -> str:
        """The revision id the promotion delivers; a promotion that does not exist is refused."""
        return self._promotion_of(ctx, ref).revision_id

    def _promotion_of(self, ctx: ApplyContext, ref: StateRef) -> GenomePromotionRow:
        """The promotion row the reference names; a promotion that does not exist is refused."""
        if not ref.reference:
            # ``genome_state`` never builds one; a hand-built or mis-decoded reference is refused here rather
            # than asked of the repository as ``promotion_id=None``.
            raise RevisionNotFound("promotion reference missing from the genome state reference")
        row = self._promotions.get(
            env=self._env(), entity_id=ctx.entity_id, bot_id=ctx.bot_id, promotion_id=ref.reference
        )
        if row is None:
            raise RevisionNotFound(f"promotion {ref.reference}")
        return row

    def check(self, ctx: ApplyContext, ref: StateRef) -> None:
        """Refuses a promotion that does not exist (``RevisionNotFound``) or whose ``from_revision`` is no longer
        ``active`` (``RefConflict``), both of which the genome's HTTP surface maps.

        The run service calls this under the bot's apply lock, before the apply is enqueued, so the second
        test closes the window in which another promotion settled between the promotion's own ref check and
        this apply taking the lock: delivering then would put a revision on the bot that settling refuses to
        name ``active``. The worker's ``compile`` asks neither question again: it maps what it is given.
        """
        row = self._promotion_of(ctx, ref)
        bot = BotRef(
            entity_id=ctx.entity_id,
            bot_id=ctx.bot_id,
            owner_id=ctx.owner_id,
            engine_type=ctx.engine_type,
            bot_type=ctx.bot_type,
        )
        active = next((r.revision for r in self._registry.list_refs(bot) if r.name == "active"), None)
        if active != row.from_revision_id:
            raise RefConflict("active", expected=row.from_revision_id, current=active)

    async def compile(
        self, ctx: ApplyContext, ref: StateRef, *, only: Collection[ApplyConstruct] | None = None
    ) -> Compiled:
        """The promotion's revision as a desired state; what ``check`` would refuse is ``CompileRefused`` here.

        ``compile`` runs in the apply worker, and it is the dry run's front
        door, with no ``check`` before it. A missing promotion, or a revision
        the state model refuses as recorded, is a reason the report (or the
        caller) should carry, not an engine failure.
        """
        bot = BotRef(
            entity_id=ctx.entity_id,
            bot_id=ctx.bot_id,
            owner_id=ctx.owner_id,
            engine_type=ctx.engine_type,
            bot_type=ctx.bot_type,
        )
        try:
            revision = self._revision_of(ctx, ref)
        except RevisionNotFound as exc:
            raise CompileRefused(f"promotion {ref.reference} is not there to deliver", domain_error=exc) from exc
        try:
            state = self._registry.compile(bot, revision)
        except NotAppliable as exc:  # the revision as recorded; a revision row that is gone is a bug and propagates
            raise CompileRefused(str(exc), domain_error=exc) from exc
        # A creation half applies its own constructs only; the rest stay undeclared.
        return Compiled(state if only is None else state.only(only))
