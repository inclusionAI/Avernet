"""Fakes for the apply engine's tests.

Each one **counts its calls**, because most of what these tests assert is the
*absence* of a write: convergence is "applying an unchanged document performs no
write", and all-or-nothing is "a category that could not be materialised wrote
nothing". Equal-looking output would prove neither.
"""

from __future__ import annotations

from tests.community.core.apply_engine._capability_facts import materialiser_ports


from agentclaw.community.core.apply_engine.context import ApplyContext


from ._fixture_data import (
    FakeActivationService,
    FakeCapabilityReader,
    FakeCredentials,
    FakeGitClient,
    FakeIdentityService,
    FakeLocalSkillDeleteService,
    FakeManifestContent,
    FakeMcpAuth,
    FakeMcpConfigService,
    FakeObjectCredentials,
    FakeObjectStore,
    FakeResourceFileService,
    FakeSharedSkills,
    FakeSkillUploadService,
    FakeStartupScriptService,
    arca_only_engine_test,
    declared_session,
    make_context,
    real_validator,
    resource_rows_for,
    seeded_object_store,
)


def device_port_bundle(
    *,
    script_service=None,
    activation_service=None,
    mcp_auth_service=None,
    mcp_config_service=None,
    identity_service=None,
    upload_service=None,
    delete_service=None,
    capability_reader=None,
    package_validator=None,
    content=None,
    resource_service=None,
    resource_rows=None,
    cli_tool_service=None,
    shared_skills=None,
):
    """The device-backed bundle, over fakes, wrapped as the root wraps it.

    The composition root builds this bundle from ten lazy providers and wraps
    four of them in the delegates that narrow a service to its port. A rig that
    built the bundle by hand would be testing the ports it chose rather than
    the ones an apply is handed, so the wrapping is reproduced here — and only
    here, rather than in each suite.

    Answers a thunk, not a bundle, because that is what a strategy takes: it is
    called once per apply, so a fake handed in is the one every apply sees.
    """
    from agentclaw.community.core.apply_engine.activation_delegates import (
        DeviceActivation,
    )
    from agentclaw.community.core.apply_engine.identity_files import (
        DeviceIdentity,
    )
    from agentclaw.community.core.apply_engine.resource_files import (
        DeviceResource,
    )
    from agentclaw.community.core.apply_engine.skill_package_upload import (
        DeviceSkillPackageUpload,
    )
    from agentclaw.community.core.bot_config_manifest.sources.source_resolver import (
        DeclaredSourceResolver,
    )

    effective_upload = upload_service or FakeSkillUploadService()
    effective_delete = delete_service or FakeLocalSkillDeleteService(effective_upload)
    bundle = materialiser_ports(
        script_service=script_service or FakeStartupScriptService(),
        activation_service=DeviceActivation(
            activation_service or FakeActivationService()
        ),
        mcp_auth_service=mcp_auth_service or FakeMcpAuth(),
        mcp_config_service=mcp_config_service or FakeMcpConfigService(),
        identity_service=DeviceIdentity(identity_service or FakeIdentityService()),
        upload_service=DeviceSkillPackageUpload(
            effective_upload,
            effective_delete,
        ),
        capability_reader=capability_reader or FakeCapabilityReader(),
        package_validator=package_validator or real_validator(),
        content=content
        or FakeManifestContent()
        or DeclaredSourceResolver(
            FakeManifestContent(), FakeCredentials(), FakeObjectStore()
        ),
        resource_service=DeviceResource(resource_service or FakeResourceFileService()),
        resource_rows=resource_rows or resource_rows_for()[0],
        cli_tool_service=cli_tool_service,
        shared_skills=shared_skills or FakeSharedSkills(),
    )
    return lambda: bundle


class UnreachableDelivery:
    """A family's row in a rig that must never route to it.

    Bound rather than omitted because ``DeliveryStrategyFactory`` refuses a
    mapping that does not name every family — the exhaustiveness that makes a
    missing family a boot error instead of a mid-apply ``KeyError``. A rig
    states "ARCA only" by binding this, and learns immediately if it was wrong.
    """

    family = "unreachable"

    def _raise(self, *_args, **_kwargs):
        raise AssertionError("this rig never routes to this engine family")

    creation_sequence = property(_raise)
    phase_of = _raise
    steps_for = _raise
    needs_container = _raise
    ports = _raise

    async def finish(self, ctx, report):
        raise AssertionError("this rig never routes to this engine family")


def delivery_strategies(*, arca=None, teclaw=None, is_teclaw=None):
    """A ``DeliveryStrategyFactory`` over the rows a rig actually exercises.

    The same construction the composition root performs: strategies built
    first, then a lookup over them. An omitted row is :class:`UnreachableDelivery`
    — not a silent ARCA fallback, which is the bug the required arguments on
    this seam exist to prevent.
    """
    from agentclaw.community.core.apply_engine.delivery import (
        DeliveryStrategyFactory,
        EngineFamily,
        family_from_engine_test,
    )

    return DeliveryStrategyFactory(
        family_of=family_from_engine_test(is_teclaw or arca_only_engine_test),
        strategies={
            EngineFamily.ARCA: arca or UnreachableDelivery(),
            EngineFamily.TECLAW: teclaw or UnreachableDelivery(),
        },
    )


def arca_only_delivery(ports):
    """The seam for a rig that exercises ARCA and nothing else."""
    from agentclaw.community.core.apply_engine.delivery import (
        ArcaDelivery,
    )

    return delivery_strategies(arca=ArcaDelivery(ports))


# ── compiling rigs: raw document entries → the typed state the engine takes ──
#
# The materialisers take typed desired entries (``core/apply_engine/desired_state``)
# and the Manifest compiler is what turns a document's raw entries into them.
# These rigs keep the suites written against raw entries honest: a raw entry
# goes through the real entry compiler, over the same fakes, and the typed
# result is what the materialiser sees — exactly the road a real apply takes.


def compile_session(ctx: ApplyContext, *, source_session=None):
    """The compiler's per-apply session over a test context."""
    from agentclaw.community.core.bot_config_manifest.compiler.session import (
        CompileSession,
    )

    return CompileSession.open(ctx, source_session=source_session or declared_session())


def fetch_context(*, source_session=None, budget=None, **context_kwargs):
    """The ``FetchContext`` a fetcher or resolver reads, built the way the compiler builds it.

    ``context_kwargs`` are ``make_context``'s; ``source_session`` and
    ``budget`` are the two fields only a compile session carries, so a suite
    driving the funnel directly names them here rather than on the apply
    context, which no longer has them.
    """
    from dataclasses import replace

    session = compile_session(
        make_context(**context_kwargs), source_session=source_session
    )
    return session if budget is None else replace(session, budget=budget)


class CompilingMaterialiser:
    """A materialiser fed through its entry compiler, the way the orchestrator feeds it.

    ``resolve(ctx, raw_entries)`` compiles the raw entries first. A compile
    failure aborts the construct before ``resolve`` runs — the orchestrator's
    rule — and is answered as a ``ResolveResult`` carrying those failures, so
    a suite reads one shape whichever side refused. ``plan`` and ``write``
    pass straight through.
    """

    def __init__(self, materialiser, compile, *, source_session=None) -> None:
        self.materialiser = materialiser
        self._compile = compile
        self._source_session = source_session
        self.last_declaration = None

    @property
    def construct(self):
        return self.materialiser.construct

    async def resolve(self, ctx: ApplyContext, entries):
        from agentclaw.community.core.apply_engine.registry import (
            ResolveFailure,
            ResolveResult,
        )

        session = compile_session(ctx, source_session=self._source_session)
        try:
            declared = await self._compile(session, entries)
        finally:
            session.close()
        self.last_declaration = declared
        if declared.failures:
            return ResolveResult(
                failures=tuple(
                    ResolveFailure(f.identity, f.reason) for f in declared.failures
                )
            )
        return await self.materialiser.resolve(ctx, declared.entries)

    async def plan(self, ctx: ApplyContext, intents):
        return await self.materialiser.plan(ctx, intents)

    async def write(self, ctx: ApplyContext, plan):
        return await self.materialiser.write(ctx, plan)


def compiling_identity(identity_service, resolver, content, *, source_session=None):
    from agentclaw.community.core.apply_engine.materialisers.identity import (
        IdentityMaterialiser,
    )
    from agentclaw.community.core.bot_config_manifest.compiler.entries.identity import (
        compile_identity,
    )

    async def compile(session, entries):
        return await compile_identity(
            session, entries, resolver=resolver, content=content
        )

    materialiser = IdentityMaterialiser(identity_service, content)
    return CompilingMaterialiser(materialiser, compile, source_session=source_session)


def compiling_skills(
    uploads,
    activation,
    reader,
    validator,
    resolver,
    content,
    *,
    source_session=None,
    shared_skills=None,
):
    from agentclaw.community.core.apply_engine.materialisers.skills import (
        SkillsMaterialiser,
    )
    from agentclaw.community.core.bot_config_manifest.compiler.entries.skills import (
        compile_skills,
    )

    async def compile(session, entries):
        return await compile_skills(
            session, entries, resolver=resolver, content=content, validator=validator
        )

    materialiser = SkillsMaterialiser(
        uploads,
        activation,
        reader,
        validator,
        content,
        shared_skills or FakeSharedSkills(),
    )
    return CompilingMaterialiser(materialiser, compile, source_session=source_session)


def compiling_resources(
    resource_service, resolver, content, *, source_session=None, resource_rows=None
):
    from agentclaw.community.core.apply_engine.materialisers.resources import (
        ResourcesMaterialiser,
    )
    from agentclaw.community.core.bot_config_manifest.compiler.entries.resources import (
        compile_resources,
    )

    async def compile(session, entries):
        return await compile_resources(
            session, entries, resolver=resolver, content=content
        )

    materialiser = ResourcesMaterialiser(
        resource_service, content, resource_rows or resource_rows_for()[0]
    )
    return CompilingMaterialiser(materialiser, compile, source_session=source_session)


def compiling_cli_tools(service, resolver, content, *, source_session=None):
    from agentclaw.community.core.apply_engine.materialisers.cli_tools import (
        CliToolsMaterialiser,
    )
    from agentclaw.community.core.bot_config_manifest.compiler.entries.cli_tools import (
        compile_cli_tools,
    )

    async def compile(session, entries):
        return await compile_cli_tools(
            session, entries, resolver=resolver, content=content
        )

    return CompilingMaterialiser(
        CliToolsMaterialiser(service), compile, source_session=source_session
    )


class EngineRig:
    """An orchestrator over a compiler: ``apply(ctx, parsed_document, …)`` the way the run service does it."""

    def __init__(self, orchestrator, compiler, *, only=None) -> None:
        self.orchestrator = orchestrator
        self.compiler = compiler
        self.only = only

    async def apply(self, ctx: ApplyContext, parsed, **kwargs):
        # A phase compiles only its own constructs, as the run service does it.
        phase = kwargs.get("phase")
        only = (
            self.only
            if phase is None
            else frozenset(step.construct for step in self.orchestrator._steps(phase))
        )
        compiled = await self.compiler.compile_document(ctx, parsed, only=only)
        return await self.orchestrator.apply(
            ctx, compiled.state, sources=compiled.sources, **kwargs
        )


def manifest_compilers(
    *, manifests, applies=None, content=None, resolver=None, git=None, validator=None
):
    """The run service's compiler map with its Manifest row, over fakes.

    ``manifests`` is the suite's document fake (``get`` / ``validate``);
    ``applies`` the apply repository the git baselines read, left out when
    the suite's document declares no sources. The content store and funnel
    default to empty fakes for the same reason.
    """
    from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
        MANIFEST_STATE,
    )
    from agentclaw.community.core.bot_config_manifest.sources.source_resolver import (
        DeclaredSourceResolver,
    )

    content = content or FakeManifestContent()
    resolver = resolver or DeclaredSourceResolver(
        content, FakeObjectCredentials(), seeded_object_store()
    )
    compiler = manifest_compiler(
        manifests=manifests,
        applies=applies,
        content=content,
        resolver=resolver,
        git=git,
        validator=validator,
    )
    return {MANIFEST_STATE.kind: compiler}


def manifest_compiler(
    *, content, resolver, validator=None, git=None, applies=None, manifests=None
):
    """A ``ManifestCompiler`` over fakes; ``compile_document`` is what the engine rigs call."""
    from agentclaw.community.core.bot_config_manifest.compiler import ManifestCompiler

    class _NoApplies:
        def recent(self, **kwargs):
            return []

    return ManifestCompiler(
        manifests=manifests,
        content=content,
        resolver=resolver,
        validator=validator or real_validator(),
        git_client_provider=git or FakeGitClient,
        applies=applies or _NoApplies(),
    )
