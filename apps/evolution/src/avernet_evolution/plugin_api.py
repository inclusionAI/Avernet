"""Version 1 platform boundary. No Backend implementation imports are permitted.

Factories are trusted deployment code, never HTTP input. Genome/Experience must
scope every access to BotRef. Production control accepts only Backend authorization receipts. AccessPort is
for explicit standalone fixtures; owner_hint is NOT an authorization decision.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from avernet_genome.models import BotRef

from .core.evolution_run.ports import (
    ExperienceQueryFactory,
    GenomePort,
    GovernancePort,
    KillSwitchesPort,
)


@dataclass(frozen=True)
class AccessGrant:
    bot: BotRef
    actor: str
    operator_user_id: str | None = None  # only from the verified built-in Backend receipt


class AccessPort(Protocol):
    async def authorize(
        self, *, bot_id: str, owner_hint: str, credentials: Mapping[str, str], write: bool
    ) -> AccessGrant:
        """Reject missing/invalid proof and cross-Bot access with PermissionError.

        credentials are ephemeral request metadata; never persist or log them.
        Background providers must use scoped service delegation, not this cookie.
        """
        ...


@dataclass
class Platform:
    access: AccessPort | None  # local fixtures only; production uses the shared Backend gate
    genome: GenomePort
    governance: GovernancePort
    kills: KillSwitchesPort
    experience: ExperienceQueryFactory
    registry: Any
    strategies: dict[str, Callable[[], Any]]
    # Production uses isolated JobWorkerSandbox workers. Each registration pins
    # its own image; each image must declare only its assigned strategy refs.
    # worker_image retains the legacy default for compatibility; it cannot
    # override a registered image or rewrite persisted version identities.
    sandbox: Any = None
    worker_image: str = ""
    #: 07 验证服务（平台所有）。None 时作业协议对候选一律回答 pending。
    verification: Any = None

    async def close(self) -> None:
        """Override to release provider resources after runner shutdown."""
