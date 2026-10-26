"""Compile a revision into the apply engine's ``DesiredState`` — the genome's compiler.

The mapping is ``api-contracts/genome/v1/manifest-v1-compatibility.md``: every
stored file becomes a ``ContentRef`` by its digest, a local skill its
``acm-tree-v1`` tree, a Skill Center skill its pinned version, a
shared-repository skill its path, MCP entries their typed override, and the
script's stored body is read back. A revision is total (contract §4.3), so
every category it covers is *declared* — an empty list declares "none", which
the engine converges to by removing what is there — and the two it never
covers (``cli_tools``, ``engine_config``) are left undeclared, so an apply
leaves them as they are. ``memory`` is not delivered until RSI-05.

``GenomeCompiler`` is the ``DesiredStateCompiler`` for the ``genome`` kind (``genome_state(promotion_id)``): a
promotion id resolves to the promotion row, whose revision compiles — while
the promotion is current. The row is ``applying`` while the delivery seam
answers and ``applied`` as soon as it has (before the apply run that delivers
it compiles in its worker), so both resolve; a promotion whose delivery never
started (``apply_failed``), or any status this module does not know, is
refused. So is a promotion the ref it moved has since moved on from: a
retried or redelivered apply must not converge the bot back to a revision
the refs no longer name. It is registered in the run service's compiler map;
RSI-04 is its first caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Collection, NoReturn

from agentclaw.community.core.apply_engine.compiler import Compiled, CompileRefused, StateRef
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
    ContentRef,
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
from agentclaw.community.core.repository.protocols.bot.genome import (
    GenomePromotionRepositoryProtocol,
    GenomeRefRepositoryProtocol,
)
from agentclaw.community.utils.env_utils import get_current_env

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentclaw.community.core.apply_engine.context import ApplyContext
    from agentclaw.community.core.bot_genome.genome_registry_protocol import GenomeRegistryProtocol

__all__ = ["GenomeCompiler", "compile_spec"]

#: The promotion statuses under which a revision is delivered; anything else never started, or is unknown here.
_DELIVERABLE = frozenset({"applying", "applied"})


def _skill_source(entry: SkillEntry) -> LocalSkillPackage | CenterSkillVersion | RepoSkillPath:
    if isinstance(entry, CenterSkill):
        return CenterSkillVersion(entry.center_version)
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


class GenomeCompiler:
    """Compiles the ``genome`` kind: the revision the referenced promotion delivers."""

    def __init__(
        self,
        *,
        registry: GenomeRegistryProtocol,
        promotions: GenomePromotionRepositoryProtocol,
        refs: GenomeRefRepositoryProtocol,
        env: Callable[[], str] = get_current_env,
    ) -> None:
        self._registry = registry
        self._promotions = promotions
        self._refs = refs
        self._env = env

    def _revision_of(self, ctx: ApplyContext, ref: StateRef) -> str:
        """The revision id the promotion delivers; a missing, never-started or superseded promotion is refused."""
        axes = dict(env=self._env(), entity_id=ctx.entity_id, bot_id=ctx.bot_id)
        row = self._promotions.get(**axes, promotion_id=ref.reference)
        if row is None:
            raise RevisionNotFound(f"promotion {ref.reference}")
        if row.status not in _DELIVERABLE:
            allowed = " or ".join(sorted(_DELIVERABLE))
            _refuse(f"promotion {ref.reference} is {row.status!r}; only an {allowed} promotion is delivered")
        current = self._refs.get(**axes, name=row.ref)
        if current is None or current.revision_id != row.revision_id:
            now = current.revision_id if current is not None else None
            _refuse(
                f"promotion {ref.reference} moved {row.ref} to {row.revision_id}, but {row.ref} is {now!r} now; "
                "a superseded promotion is not delivered"
            )
        return row.revision_id

    def check(self, ctx: ApplyContext, ref: StateRef) -> None:
        """Raises the genome's own errors (``RevisionNotFound``, ``NotAppliable``), which its HTTP surface maps."""
        self._revision_of(ctx, ref)

    async def compile(
        self, ctx: ApplyContext, ref: StateRef, *, only: Collection[ApplyConstruct] | None = None
    ) -> Compiled:
        """The promotion's revision as a desired state; what ``check`` would refuse is ``CompileRefused`` here.

        ``compile`` runs in the apply worker, after ``check`` accepted the
        reference and an apply id was minted — the refs can have moved in
        between, and a revision can fail the state model as recorded — and
        it is the dry run's front door, with no ``check`` before it. Either
        way the refusal is a reason the report (or the caller) should carry,
        not an engine failure.
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
        except NotAppliable as exc:
            raise CompileRefused(str(exc), domain_error=exc) from exc
        try:
            state = self._registry.compile(bot, revision)
        except NotAppliable as exc:  # the revision as recorded; a revision row that is gone is a bug and propagates
            raise CompileRefused(str(exc), domain_error=exc) from exc
        # A creation half applies its own constructs only; the rest stay undeclared.
        return Compiled(state if only is None else state.only(only))


def _refuse(message: str) -> NoReturn:
    raise NotAppliable([Reason("not_appliable", "promotion", message)])
