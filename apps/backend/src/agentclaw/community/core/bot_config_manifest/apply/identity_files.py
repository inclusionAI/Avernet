"""The device-backed ``IdentityFilePort``: identity files on the container.

``IdentityService`` writes a bot's identity files into its live container. This
is the apply engine's view of it — the three methods the ``identity``
materialiser calls, and not the other twelve.

**The narrowing is the job.** The service exposes entity files as well as bot
files, path resolution, validation and ``sync_agents_md``; a materialiser
handed the whole service could reach any of them. Handed the port, it cannot.
Same narrowing ``ActivationPort`` performs on ``project`` and
``SkillPackageUploadPort`` on ``upload_local_skill_files``, at a larger ratio.

**The wrapped service is imported under ``TYPE_CHECKING`` only, and that is
load-bearing.** ``core/services/identity.py`` reaches the device dispatcher
graph at module scope, and importing it eagerly from here — this module is
imported by the apply service, which the DI graph builds — walks that chain
into a circular import. The DI provider that supplies the service takes the
same precaution with a function-level import, and records why.

Because that service is deliberately protocol-less (one implementation; the
identity router records the waiver), the annotation below is the only thing
naming what this forwards to, and the DI provider's wiring-time structural
check is the only guard against it drifting. Both are needed: the annotation
does not run, and the check cannot see signatures.

Its platform-managed counterpart is ``PlatformIdentity`` in
``managed_files/ports.py``, which writes the same files as store objects.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentclaw.community.core.ports.identity_file_port import IdentityFilePort

if TYPE_CHECKING:  # pragma: no cover — see the module docstring
    from agentclaw.community.core.services.identity import IdentityService


class DeviceIdentity(IdentityFilePort):
    """The ARCA identity road: files onto the bot's live container.

    Three methods, each forwarded verbatim, and the only one in this package
    whose arguments are **positional**. What a call looks like from the
    ``identity`` materialiser::

        await port.list_bot_files(
            "staff", "ent_7", "bot_42", "usr_owner",
            engine_type="claude_code", stage="draft",
        )   # -> [("SOUL.md", True), ("RULES.md", False), ...]
            #    one (file_type, exists) pair per name in
            #    VALID_IDENTITY_FILES, the WHOLE whitelist, not just the
            #    files that exist. "exists" is bool(content), so an empty
            #    file reports False.

        await port.read_identity_file(
            "staff", "ent_7", "bot_42", "SOUL.md", "usr_owner",
            engine_type="claude_code", stage="draft",
        )   # -> the file's text, or "" when it is missing

        await port.update_bot_file(
            "staff", "ent_7", "bot_42", "SOUL.md", "# who I am\n",
            "usr_collaborator",           # the operator: the ACTOR, not owner
            "claude_code", stage="draft",
        )

    The ``file_type`` is a whitelisted filename — ``"SOUL.md"``,
    ``"RULES.md"``, ``"MEMORY.md"`` and so on — which is also the ``type`` an
    identity entry declares and the ``identity`` its report row carries.
    Content is ``str`` here, not ``bytes``: identity files are text. Removal is
    an empty write, because the domain reads absent and empty as one state.

    Bound as ``MaterialiserPorts.identity_service`` for the ARCA family. Its
    platform-managed counterpart is ``PlatformIdentity`` in
    ``managed_files/ports.py``.
    """

    def __init__(self, inner: IdentityService) -> None:
        self._inner = inner

    async def list_bot_files(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
        stage: str = "draft",
    ) -> list[tuple[str, bool]]:
        return await self._inner.list_bot_files(
            entity_type, entity_id, bot_id, owner_id,
            engine_type=engine_type, stage=stage,
        )

    async def read_identity_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
        stage: str = "draft",
    ) -> str:
        return await self._inner.read_identity_file(
            entity_type, entity_id, bot_id, file_type, owner_id,
            engine_type=engine_type, stage=stage,
        )

    async def update_bot_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        content: str,
        operator_id: str,
        engine_type: str | None = None,
        *,
        stage: str = "draft",
    ) -> Any:
        return await self._inner.update_bot_file(
            entity_type, entity_id, bot_id, file_type, content, operator_id,
            engine_type, stage=stage,
        )


__all__ = ["DeviceIdentity"]
