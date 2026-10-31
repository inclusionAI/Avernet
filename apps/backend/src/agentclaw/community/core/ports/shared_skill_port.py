"""Finding a shared skill a bot may install: a Skill Center skill by name, a shared-repository skill by path.

An outbound port (see this package's README): the ``skills`` materialiser asks
it to turn a desired Center or repository skill into the skill id the
Direct-claim write takes. A revision names a Center skill by its runtime name
only (the bot runs whatever version the Center publishes, ADR 0002) and a
repository skill by its path under ``git://``, so the lookup is the
materialiser's one question; visibility and access are the Skill Center's
own rule, asked for the bot and the apply's principal.

Implemented by ``SkillQueryService`` (it owns Local, Repo and Center
addressing and asset visibility); a fake implements it for the apply-engine
suites, pinned to this protocol by a reflection test.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

__all__ = ["SharedSkillAmbiguous", "SharedSkillNotFound", "SharedSkillPort"]


class SharedSkillNotFound(LookupError):
    """No shared skill of that name or path is installable on this bot; the message says which."""


class SharedSkillAmbiguous(LookupError):
    """More than one Center skill of that name is installable on this bot; delivery does not guess."""


@runtime_checkable
class SharedSkillPort(Protocol):
    @abstractmethod
    def find_center_skill(self, *, bot_id: str, owner_id: str, actor_id: str, name: str) -> str:
        """The id of the one online Center skill named ``name`` the bot and ``actor_id`` may use.

        Raises ``SharedSkillNotFound`` when there is none — a public market
        skill never imported into this tenant included; delivery starts no
        import — and ``SharedSkillAmbiguous`` when there are several.
        """
        ...

    @abstractmethod
    def find_repo_skill(self, *, bot_id: str, owner_id: str, actor_id: str, name: str, path: str) -> str:
        """The id of the shared-repository skill at ``git://<path>``, whose runtime name must be ``name``.

        Raises ``SharedSkillNotFound`` when there is none, or when the skill there is named otherwise: the
        entry, the report and the runtime name must agree, as for a local package.
        """
        ...
