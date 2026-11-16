"""真实 Bot 基因组注册表适配器：通过 HTTP 调用平台 OpenAPI，实现 06 需要的 `genome` 端口。

把用户提供的 bot genome OpenAPI 接入 06 的
`build_evolution_runtime(..., genome=RealBotGenome(...))`，
供 01-06 核心链路（提交→运行→策略执行→候选提交）使用真实 bot 数据跑通。

实现的方法（06 核心链路真正需要的）：
  - drift(bot)            -> Drift | None
  - resolve_revision(bot, ref) -> GenomeRevision   （支持 id / active / seq）
  - snapshot_files(revision, bot) -> dict[str, bytes]   （展开 persona/skills/resources）

未实现的方法（需平台补充 OpenAPI，否则抛 NotImplementedError）：
  commit_patch / set_policy / set_status / diff / list_refs / move_ref /
  put_content / compile（候选落库与晋升由后续 07/08 补齐）。

配置项（在你内网环境里填）：
  BOT_GENOME_BASE_URL   平台 openapi 前缀，例如 https://<gw-host>/openapi/v1
  BOT_GENOME_USER_ID    479275
  BOT_GENOME_AUTH_TOKEN IAM token（走 Cookie: IAM_TOKEN）
  BOT_GENOME_REFERER    可选；缺省取 base_url 的 origin
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from agentclaw.community.core.bot_genome.errors import ContentNotReachable, RevisionNotFound
from agentclaw.community.core.bot_genome.gene_paths import (
    persona_path,
    resource_path,
    skill_path,
)
from agentclaw.community.core.bot_genome.models import (
    BotRef,
    Drift,
    GenomeRevision,
    Provenance,
    ProvenanceKind,
    RevisionMetadata,
    RevisionStatus,
)
from agentclaw.community.core.bot_genome.patch_models import GenomePatch
from agentclaw.community.core.bot_genome.spec_models import GenomePolicy, GenomeSpec


def _origin(base_url: str) -> str:
    """把 base_url 还原成 origin（scheme://host[:port]），用作 Referer 的中性默认值。"""
    if "://" in base_url:
        scheme, rest = base_url.split("://", 1)
        host = rest.split("/", 1)[0]
        return f"{scheme}://{host}"
    return base_url


def _parse_created_at(value: str) -> datetime:
    """兼容 `2026-11-12T08:25:16Z`（Python 3.9 不识别尾部的 Z）。"""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class RealBotGenome:
    """HTTP 支持的 genome 端口：对接平台 OpenAPI 的 bot genome 接口。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        user_id: str | None = None,
        auth_token: str | None = None,
        referer: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._base = (base_url or os.environ.get("BOT_GENOME_BASE_URL") or "").rstrip("/")
        self._user_id = user_id or os.environ.get("BOT_GENOME_USER_ID") or ""
        self._auth_token = auth_token or os.environ.get("BOT_GENOME_AUTH_TOKEN") or ""
        self._referer = referer or os.environ.get("BOT_GENOME_REFERER") or _origin(self._base)
        self._client = client or httpx.Client(timeout=60.0)
        self._last_bot_id: str | None = None

    # -- internal -----------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._referer:
            headers["Referer"] = self._referer
        if self._auth_token:
            headers["Cookie"] = "IAM_TOKEN=" + self._auth_token
        return headers

    def _params(self) -> dict[str, str]:
        return {"user_id": self._user_id} if self._user_id else {}

    def _unwrap(self, payload: dict[str, Any]) -> dict[str, Any]:
        """平台响应包在 {"code":..,"message":..,"data":{...}} 里。"""
        if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
            return payload["data"]
        return payload

    def _parse_revision(self, bot: BotRef, payload: dict[str, Any]) -> GenomeRevision:
        data = self._unwrap(payload)
        meta = data.get("revision", {})
        created_by_raw = meta.get("created_by", {}) or {}
        return GenomeRevision(
            genome_schema=int(data.get("genome_schema", 1)),
            revision=RevisionMetadata(
                id=meta["id"],
                seq=int(meta.get("seq", 1)),
                parent=meta.get("parent"),
                created_by=Provenance(
                    kind=ProvenanceKind(created_by_raw.get("kind", "user")),
                    actor=created_by_raw.get("actor", ""),
                    run_id=created_by_raw.get("run_id"),
                    step=created_by_raw.get("step"),
                ),
                created_at=_parse_created_at(meta.get("created_at", "1970-01-01T00:00:00+00:00")),
                status=RevisionStatus(meta.get("status", "draft")),
                patch_digest=meta.get("patch_digest"),
                evidence=tuple(meta.get("evidence", []) or []),
            ),
            spec=GenomeSpec.from_json(data.get("spec", {})),
            policy=GenomePolicy.from_json(data.get("policy", {})),
        )

    async def _http_get_json(self, path: str) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.get(
                self._url(path), params=self._params(), headers=self._headers()
            ),
        )
        if response.status_code == 404:
            raise RevisionNotFound(path)
        response.raise_for_status()
        return self._unwrap(response.json())

    async def _http_get_bytes(self, path: str) -> bytes:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.get(
                self._url(path), params=self._params(), headers=self._headers()
            ),
        )
        if response.status_code == 404:
            raise ContentNotReachable(path)
        response.raise_for_status()
        return response.content

    # -- 06 genome port -----------------------------------------------------

    async def drift(self, bot: BotRef) -> Drift | None:
        # 平台未提供「当前状态对比」API 时，按「无漂移」返回，让运行能继续进入。
        # 如有平台的 bot 当前状态接口，在此把 captured spec 与 active revision id 比较。
        return None

    async def resolve_revision(self, bot: BotRef, ref: str) -> GenomeRevision:
        self._last_bot_id = bot.bot_id
        path = f"/bots/{bot.bot_id}/genome/revisions/{ref}"
        payload = await self._http_get_json(path)
        return self._parse_revision(bot, payload)

    async def snapshot_files(
        self, revision: GenomeRevision, *, bot: BotRef | None = None
    ) -> dict[str, bytes]:
        """把 GenomeSpec 里的每个文件 digest 展开成本地字节，供沙箱工作区用。

        ``context.build_run_context`` 只传 revision、不传 bot；此时回退到最近一次
        ``resolve_revision`` 记下的 bot_id（同一 adapter 一次服务一个 bot）。
        """

        bot_id = bot.bot_id if bot is not None else self._last_bot_id
        if bot_id is None:
            raise RuntimeError("snapshot_files needs a bot; call resolve_revision first or pass bot=")

        spec = revision.spec
        files: dict[str, bytes] = {}
        for entry in spec.persona:
            files[persona_path(entry.type)] = await self._http_get_bytes(
                f"/bots/{bot_id}/genome/content/{entry.digest}"
            )
        for entry in spec.skills:
            if getattr(entry, "origin", None) != "local":
                continue
            files[skill_path(entry.name, "SKILL.md")] = await self._http_get_bytes(
                f"/bots/{bot_id}/genome/content/{entry.digest}"
            )
        for entry in spec.resources:
            path = getattr(entry, "path", "")
            if path and not path.endswith("/"):
                files[resource_path(path)] = await self._http_get_bytes(
                    f"/bots/{bot_id}/genome/content/{entry.digest}"
                )
        return files

    async def read_content(self, bot: BotRef, digest: str) -> bytes:
        return await self._http_get_bytes(f"/bots/{bot.bot_id}/genome/content/{digest}")

    def _sync_read_bytes(self, bot_id: str, digest: str) -> bytes:
        response = self._client.get(
            self._url(f"/bots/{bot_id}/genome/content/{digest}"),
            params=self._params(),
            headers=self._headers(),
        )
        if response.status_code == 404:
            raise ContentNotReachable(digest)
        response.raise_for_status()
        return response.content

    # -- 01-genome 候选记录（本地内容寻址，不写平台 genome）-----------------------

    async def commit_patch(
        self,
        bot: BotRef,
        patch: GenomePatch,
        run_id: str,
        strategy_ref: str,
        allowed_genes: tuple[str, ...],
        created_by: Provenance,
    ) -> str:
        """把补丁作用在基准修订上，返回新修订的内容寻址 id。

        平台尚未提供 patch 提交 OpenAPI：这里在本地完成 apply 并按
        ``JCS({spec, policy})`` 内容寻址，只返回 revision id，不把新修订
        写回平台基因组。真实持久化需平台补齐 patch 提交端点后接入。
        """

        from agentclaw.community.core.bot_genome.canonical import revision_id
        from agentclaw.community.core.bot_genome.patch.ops import apply_patch

        base = await self.resolve_revision(bot, patch.base)
        owner, bot_id = self, bot.bot_id

        class _Reader:
            def read(self, digest: str) -> bytes:
                return owner._sync_read_bytes(bot_id, digest)

        outcome = apply_patch(base.spec, patch.ops, _Reader())
        return revision_id(outcome.spec, base.policy)
