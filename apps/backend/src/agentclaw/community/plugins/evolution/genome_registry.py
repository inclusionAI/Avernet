"""生产基因组注册表向本服务沙箱端口的适配器（01-genome ↔ 06-evolution-run）。

`JobChannel.files` 需要把父修订展开成工作区快照，`JobChannel.submit` 需要把候补
记成 `candidate` 修订；这里把 production `GenomeRegistryProtocol` 包成 `GenomePort`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.bot_genome.gene_paths import (
    persona_path,
    resource_path,
    skill_path,
)
from agentclaw.community.core.bot_genome.models import (
    BotRef,
    GenomeRevision,
    Provenance,
    ProvenanceKind,
    RevisionStatus,
    RunScope,
)
from agentclaw.community.core.bot_genome.patch_models import GenomePatch
from agentclaw.community.core.content_store import decode_tree_bytes
from avernet_genome.mutation_scope import FrozenMutationScope

__all__ = ["RegistryGenomePort"]


@dataclass(frozen=True)
class RegistryGenomePort:
    """把 production 注册表当作沙箱里的基因组读写面。

    展开规则：``persona/{type}``、``skills/{name}/SKILL.md``、``resources/{path}``
    的字节按摘要从内容存储读取；center/repo 技能不拷进沙箱（定义对策略只读）。
    """

    registry: Any

    async def drift(self, bot: BotRef) -> Any:
        return await self.registry.drift(bot)

    async def resolve_revision(self, bot: BotRef, ref: str) -> GenomeRevision:
        return self.registry.get_revision(bot, ref)

    async def read_content(self, bot: BotRef, digest: str) -> bytes:
        return self.registry.read_content(bot, digest)

    async def snapshot_files(
        self, revision: GenomeRevision, *, bot: BotRef
    ) -> dict[str, bytes]:
        spec = getattr(revision, "spec", None)
        if spec is None:
            raise TypeError("revision must carry a genome spec to be materialised")
        files: dict[str, bytes] = {}
        for entry in spec.persona:
            files[persona_path(entry.type)] = self.registry.read_content(
                bot, entry.digest
            )
        for entry in spec.skills:
            if getattr(entry, "origin", None) != "local":
                continue
            tree = decode_tree_bytes(self.registry.read_content(bot, entry.digest))
            if tree is None:
                raise ValueError(
                    f"local skill {entry.name!r} is not an acm-tree-v1 content blob"
                )
            for member, data in tree:
                files[skill_path(entry.name, member)] = data
        for entry in spec.resources:
            path = getattr(entry, "path", "")
            if path and not path.endswith("/"):
                files[resource_path(path)] = self.registry.read_content(
                    bot, entry.digest
                )
        return files

    async def commit_patch(
        self,
        bot: BotRef,
        patch: GenomePatch,
        run_id: str,
        strategy_ref: str,
        allowed_genes: tuple[str, ...],
        created_by: str,
        *,
        mutation_scope: FrozenMutationScope | None = None,
    ) -> str:
        recorded = await self.registry.commit_patch(
            bot,
            patch,
            created_by=Provenance(
                kind=ProvenanceKind.STRATEGY_RUN, actor=created_by, run_id=run_id
            ),
            status=RevisionStatus.CANDIDATE,
            run=RunScope(
                run_id=run_id,
                strategy=strategy_ref,
                allowed_genes=allowed_genes,
                mutation_scope=mutation_scope,
            ),
        )
        return recorded.revision.revision.id

    async def move_ref(
        self, bot: BotRef, ref: str, revision_id: str, actor: str
    ) -> None:
        """晋升路径尚未挂到这里：ref 移动必须带晋升记录，不能由作业面代替。"""

        del bot, ref, revision_id, actor
        raise NotImplementedError(
            "ref moves belong to the promotion path (08-promotion), not to the job channel"
        )
