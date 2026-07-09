"""LocalDeviceSyncPlugin -- local-mode implementation of DeviceSyncPlugin.

Creates/removes symlinks in ~/.openclaw/workspace/skills/ based on the
symlink mappings received from SkillSetActivator/SkillSetSwitcher.
"""

import shutil
from pathlib import Path
from typing import Any, Optional

import httpx

from agentclaw.community.core.devices.models import DeviceBindingContext
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_sync import (
    DeviceSyncPlugin,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam

logger = get_logger()

RESERVED_NAMES = frozenset([
    "skills-repo",
    "skills-local",
    ".current_skill_set",
    "skill_sets.json",
])


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="skills_dir=None -> all methods no-op",
)
class LocalDeviceSyncPlugin(MockSeam, DeviceSyncPlugin):
    """Local mode: creates/removes symlinks directly on the local filesystem.

    ``skills_dir`` should point to ``~/.openclaw/workspace/skills/``
    (the directory OpenClaw actually reads via workspaceSkills priority).

    When ``skills_dir`` is *None*, all operations are no-ops so the plugin
    can be safely used as a fallback.
    """

    # Candidate source directories for skills-repo, in priority order.
    # GitSyncService rsyncs to ~/aiworkbench/skills-repo (local disk, fast).
    # OSS sync copies to bolt_shared/skills-repo (NAS, slower but persistent).
    _REPO_SOURCE_CANDIDATES = (
        Path.home() / "aiworkbench" / "skills-repo",
    )

    def __init__(
        self,
        skills_dir: Optional[Path] = None,
        *,
        baas_service: Optional[BaasService] = None,
        binding_ctx: Optional[DeviceBindingContext] = None,
    ):
        """Dual-mode ctor:

        - **BaaS mode** (singlebox prod): both ``baas_service`` and
          ``binding_ctx`` set → ``sync_symlinks`` POSTs to container's
          ``/api/skills/symlink`` via plan-01 ``get_http_info``.
        - **Pathlib fallback** (contract tests / standalone host use):
          either ctor arg missing → original ``shutil`` + ``Path.symlink_to``
          path against ``skills_dir`` (preserves all existing behavior).

        ``skills_dir`` is still consumed in pathlib mode; in BaaS mode it's
        ignored (the container's skills dir is fixed on the device side).
        """
        self._skills_dir = Path(skills_dir) if skills_dir else None
        self._baas_service = baas_service
        self._binding_ctx = binding_ctx
        self._is_baas_mode = baas_service is not None and binding_ctx is not None

    def sync_symlinks(
        self,
        symlinks: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Full-sync — dispatches to BaaS or pathlib per ctor mode."""
        if self._is_baas_mode:
            return self._baas_sync_symlinks(symlinks)
        return self._pathlib_sync_symlinks(symlinks)

    def _baas_sync_symlinks(
        self,
        symlinks: list[dict[str, str]],
    ) -> dict[str, Any]:
        """BaaS 模式：调 adapter bindpath/clean 接口对齐线上 ArcaDeviceSyncPlugin。

        - 非空 list → POST ``/api/skills/symlink/bindpath``，
          body ``{symlinks, clean_target_dir: True}``
          (source/target 保持绝对路径，由 adapter ``_skills_validate_absolute_path`` 校验)
        - 空 list → POST ``/api/skills/symlink/clean``，
          body ``{directories: ["/home/admin/.openclaw/workspace/skills"]}``
          (清掉 skills 目录下所有孤儿软链)

        历史背景：曾经调 ``/api/skills/symlink``，该接口要求 source 是相对 base
        目录 (``SKILLS_LINK_BASE_DIR``，已废弃) 的路径，跟 backend 推的绝对路径
        不兼容、跟线上 ArcaDeviceSyncPlugin 行为也不一致。R2 重跑 C016 时被 adapter
        返 400 `"source 必须是相对 base 目录的路径"` 暴露。

        非 2xx 或 BaaS 失败 → ``{"success": False, "message": ...}`` (sync 契约
        保持原 dict shape，不 raise，spec §4.2)。
        """
        # 过滤 source/target 为空的条目 (对齐线上 _infer_and_clean_symlinks)
        cleaned: list[dict[str, str]] = []
        for sm in symlinks:
            src = sm.get("source", "")
            tgt = sm.get("target", "")
            if not src or not tgt:
                logger.warning(
                    "[LocalDeviceSyncPlugin.baas.sync_symlinks] skip invalid: %s", sm
                )
                continue
            cleaned.append({"source": src, "target": tgt})

        # 空 list (能力集移除最后一个 skill 后,空 desired): 不调 adapter clean 接口,
        # 直接 return success。原因:
        # - LocalDeviceSyncPlugin 只服务 singlebox,宿主 skills/ 下除了 skill 软链还有
        #   skills-repo (symlink → ~/aiworkbench/skills-repo) 和 skills-local (真目录),
        #   adapter clean_symlinks 不区分是 skill 还是系统软链,会把 skills-repo 一起清掉。
        # - 孤儿 skill 链的清理由上层 SkillSetService.deactivate_skill 在 add/remove
        #   skill 时单删处理。
        if not cleaned:
            logger.info(
                "[LocalDeviceSyncPlugin.baas.sync_symlinks] empty list, "
                "skip clean (orphans handled by SkillSetService.deactivate_skill), "
                "binding=%s",
                self._binding_ctx.binding_id,
            )
            return {
                "success": True,
                "message": "empty list, skip adapter clean",
                "total": 0, "created": [], "updated": [], "kept": [], "removed": [],
            }

        api_path = "/api/skills/symlink/bindpath"
        # clean_target_dir=False: 不让 adapter 自动清掉 target 目录里非 desired 的软链。
        # 线上 prod 容器内 skills/ 下只有 skill 链接,clean=True 是干净的;但 singlebox
        # 宿主的 skills/ 下还有 skills-repo / skills-local 这类系统级软链/目录,clean
        # 会把它们误删。孤儿清理由上层 SkillSetService.deactivate_skill 单删完成。
        request_body = {
            "symlinks": cleaned,
            "clean_target_dir": False,
        }
        logger.info(
            "[LocalDeviceSyncPlugin.baas.sync_symlinks] %d mappings → bindpath, binding=%s",
            len(cleaned), self._binding_ctx.binding_id,
        )

        try:
            info = self._baas_service.get_http_info(
                bind_id=self._binding_ctx.binding_id,
                port=self._binding_ctx.adapter_port,
                path=api_path,
                device_affinity=self._binding_ctx.entity_id,
                tenant=self._binding_ctx.tenant or None,
            )
        except Exception as e:
            logger.error(
                "[LocalDeviceSyncPlugin.baas.sync_symlinks] get_http_info: %s", e
            )
            return {
                "success": False,
                "message": f"BaaS get_http_info failed: {e}",
            }

        url = info.http_url
        headers = {"openclawToken": info.token}

        async def _run() -> dict[str, Any]:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url, json=request_body, headers=headers
                )
            return _parse(response)

        def _parse(response: httpx.Response) -> dict[str, Any]:
            if response.status_code >= 400:
                return {
                    "success": False,
                    "message": (
                        f"container HTTP {response.status_code}: "
                        f"{response.text[:200]}"
                    ),
                }
            body = response.json() if response.content else {}
            data = body.get("data", {}) if isinstance(body, dict) else {}
            return {
                "success": True,
                "message": (
                    f"baas sync done: total={data.get('total', 0)}, "
                    f"created={len(data.get('created', []))}, "
                    f"updated={len(data.get('updated', []))}, "
                    f"kept={len(data.get('kept', []))}, "
                    f"removed={len(data.get('removed', []))}"
                ),
                **data,
            }

        # sync_symlinks 是 sync 签名 (Protocol)；新线程跑独立 loop 避免死锁
        # (调用方在 anyio.to_thread 持当前 loop，绝不能 run_coroutine_threadsafe 提回当前 loop)
        try:
            import asyncio
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: asyncio.run(_run())).result(timeout=60)
        except Exception as e:  # pragma: no cover — defensive log+wrap, real path covered by acceptance
            logger.error(
                "[LocalDeviceSyncPlugin.baas.sync_symlinks] container call: %s", e
            )
            return {"success": False, "message": f"container call failed: {e}"}

    def _pathlib_sync_symlinks(
        self,
        symlinks: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Full-sync: make the skills/ directory match *symlinks* exactly.

        1. Create/replace every symlink in *symlinks*.
        2. Remove any existing symlink in skills/ that is NOT in *symlinks*
           and NOT a reserved name.

        Each entry in *symlinks*: ``{"source": "./skills-repo/...", "target": "link-name"}``
        """
        if self._skills_dir is None:
            logger.info("[LocalDeviceSyncPlugin.sync_symlinks] No skills_dir — skipping (non-local device)")
            return {"success": True, "message": "no skills_dir configured, skip"}

        logger.info(
            "[LocalDeviceSyncPlugin.sync_symlinks] %d mappings, skills_dir=%s",
            len(symlinks), self._skills_dir,
        )

        self._skills_dir.mkdir(parents=True, exist_ok=True)

        # Ensure skills-repo is rsynced into skills_dir before creating symlinks
        self._ensure_skills_repo()

        desired_names: set[str] = set()
        created = 0
        skipped = 0
        for mapping in symlinks:
            source = mapping.get("source", "")
            target = mapping.get("target", "")
            if not source or not target:
                logger.warning("[LocalDeviceSyncPlugin] skipping invalid mapping: %s", mapping)
                continue

            # 将容器内路径转换为本地路径
            source_path = self._resolve_local_path(source)
            link_path = self._resolve_local_path(target)

            # 从 link_path 提取 target_name（用于日志和去重）
            target_name = link_path.name
            desired_names.add(target_name)

            if not source_path.exists():
                logger.warning("[LocalDeviceSyncPlugin] source does not exist, skipping: %s (original: %s)", source_path, source)
                skipped += 1
                continue

            if link_path.is_symlink() or link_path.exists():
                if link_path.is_symlink() or link_path.is_file():
                    link_path.unlink()
                else:
                    shutil.rmtree(link_path)

            try:
                # 使用相对路径创建软链
                try:
                    rel_source = source_path.relative_to(self._skills_dir)
                    link_path.symlink_to(rel_source)
                    logger.debug("[LocalDeviceSyncPlugin] created: %s -> ./%s", target_name, rel_source)
                except ValueError:
                    # 无法计算相对路径，使用绝对路径
                    link_path.symlink_to(source_path)
                    logger.debug("[LocalDeviceSyncPlugin] created: %s -> %s", target_name, source_path)
                created += 1
            except OSError as e:
                logger.error("[LocalDeviceSyncPlugin] failed to create symlink %s -> %s: %s", target_name, source_path, e)
                skipped += 1

        removed = 0
        for item in self._skills_dir.iterdir():
            if item.name in RESERVED_NAMES:
                continue
            if item.name in desired_names:
                continue
            if item.is_symlink():
                item.unlink()
                removed += 1
                logger.debug("[LocalDeviceSyncPlugin] removed stale symlink: %s", item.name)

        logger.info(
            "[LocalDeviceSyncPlugin.sync_symlinks] done: created=%d, removed=%d, skipped=%d",
            created, removed, skipped,
        )
        return {
            "success": True,
            "message": f"local sync done: created={created}, removed={removed}, skipped={skipped}",
            "created": created,
            "removed": removed,
            "skipped": skipped,
        }

    def sync_bot_config(
        self,
        bot_id: str,
        binding_id: int,
        public: str,
        permission_owner: Optional[str],
        user_id: str,
        nick_name: str,
    ) -> dict[str, Any]:
        """Noop in local mode — the target ``/api/bot/config`` endpoint
        runs inside the device sandbox, which isn't reachable on a dev
        laptop. Returns a sentinel matching today's pre-Rule-14 behavior.
        """
        logger.info(
            "[LocalDeviceSyncPlugin.sync_bot_config] bot=%s binding_id=%s — "
            "local mode, skip",
            bot_id, binding_id,
        )
        return {
            "success": False,
            "message": "local mode — device sync skipped",
        }

    # ── MCP delivery — no-op in local mode (no remote device to push to) ──
    # Mirrors the former NoopDeviceMCPSyncPlugin: every method reports success.
    def sync_all_mcp_servers(self, mcp_servers: list[dict[str, Any]]) -> bool:
        return True

    def sync_single_mcp(
        self,
        mcp_data: dict[str, Any],
        *,
        api_key: Optional[str] = None,
        custom_headers: Optional[dict[str, str]] = None,
        endpoint_env: str = "PROD",
        transport_protocol: Optional[str] = None,
    ) -> bool:
        return True

    def sync_remove_mcp(self, server_code: str) -> bool:
        return True

    def has_mcp(self, server_code: str) -> bool:
        # No remote device in local mode; report "present" so the (no-op)
        # batch push path is uniform.
        return True

    def _ensure_skills_repo(self) -> None:
        """Symlink skills-repo into skills_dir (singlebox mount 模拟).

        线上：backend clone → 推 OSS → 容器 fuse mount skills-repo。
        singlebox：全本机，"mount" 等价于把 GitSyncService clone 出的
        ``~/aiworkbench/skills-repo`` 软链到生效目录
        ``~/.openclaw/workspace/skills/skills-repo``——同一份内容两处可见，
        源更新即时生效，无副本/增量维护。

        本地模式：源不存在则建空目录兜底，软链创建不抛错。
        """
        target = self._skills_dir / "skills-repo"
        source = self._find_repo_source()

        if source is None:
            # 源不存在：留旧目录/旧链，没有就建空目录兜底
            if not target.exists():
                target.mkdir(parents=True, exist_ok=True)
                logger.info("[LocalDeviceSyncPlugin] Created empty skills-repo directory (local dev mode)")
            return

        # 已正确软链到源 → 幂等返回
        if target.is_symlink() and target.resolve() == source.resolve():
            logger.info("[LocalDeviceSyncPlugin] skills-repo already symlinked -> %s", source)
            return

        # 清旧（旧链 / 旧真目录拷贝），再软链到源
        if target.is_symlink() or target.exists():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.symlink_to(source)
        logger.info("[LocalDeviceSyncPlugin] symlink skills-repo: %s -> %s", target, source)

    def _find_repo_source(self) -> Optional[Path]:
        """Return the first existing skills-repo source directory."""
        for candidate in self._REPO_SOURCE_CANDIDATES:
            if candidate.is_dir():
                return candidate
        # Fallback: try bolt_shared (OSS sync target)
        try:
            from agentclaw.community.core.workspace.path_factory import get_global_skills_repo_dir
            shared = get_global_skills_repo_dir()
            if shared.is_dir():
                return shared
        except Exception:
            pass
        return None

    def _resolve_local_path(self, path: str) -> Path:
        """将容器内的绝对路径转换为本地主机路径。

        get_symlink_mappings() 返回的路径是 Arca 容器内的绝对路径，不同引擎使用不同前缀：
        - /home/admin/.claude_code/skills/skills-repo/...
        - /home/admin/.aicoding/skills/skills-repo/...
        - /home/admin/.openclaw/workspace/skills/skills-repo/...
        以及对应的 target 路径。

        本地模式下统一映射到 self._skills_dir 下的相对路径。
        """
        # 容器内路径前缀（按长度降序匹配，避免前缀冲突）
        _CONTAINER_PREFIXES = [
            "/home/admin/.claude_code/skills/",
            "/home/admin/.aicoding/skills/",
            "/home/admin/.openclaw/workspace/skills/",
        ]

        for prefix in _CONTAINER_PREFIXES:
            if path.startswith(prefix):
                relative_path = path[len(prefix):]
                return self._skills_dir / relative_path

        # 已经是相对路径或本地绝对路径，直接返回
        return Path(path)
