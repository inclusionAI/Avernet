"""Bot Discover Service.

封装 BCSFuse 推荐，并通过 BCS Catalog 过滤当前 viewer 不可见的 Bot。
"""
import json as _json
from typing import Any, Optional, TYPE_CHECKING

import requests

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.log import get_logger
from agentclaw.community.core.bot_public.bot_discover_service_protocol import BotDiscoverServiceProtocol
from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogCaller,
    BotCatalogMetadata,
    BotCatalogMetadataServiceProtocol,
    BotCatalogSearchFilters,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

if TYPE_CHECKING:
    # Type-only: runtime ``from agentclaw.community.di import config`` would form a
    # cycle (di/__init__ -> container -> bot_public_module ->
    # bot_discover_service). BotDiscoverService is @provider-constructed
    # (bot_public_module), so this annotation is never resolved at
    # runtime.
    from agentclaw.community.di import config as cfg

logger = get_logger()


class BotDiscoverService(BotDiscoverServiceProtocol):
    """Bot Discover 服务.

    封装 BCSFuse recommend 接口的调用，并使用 BCS Catalog 作为可见性权威源。

    """

    def __init__(
        self,
        bot_repository: BotRepository,
        bcsfuse_config: "cfg.BcsFuseConfig",
        catalog_metadata_service: BotCatalogMetadataServiceProtocol,
    ) -> None:
        self._bot_repository = bot_repository
        self._bcsfuse_config = bcsfuse_config
        self._catalog_metadata_service = catalog_metadata_service
        self._bcsfuse_base_url = self._resolve_bcsfuse_base_url()

    def _resolve_bcsfuse_base_url(self) -> str:
        """Pick BCSFuse base URL from typed config (pre vs default)."""
        from agentclaw.community.utils.env_utils import get_current_env

        env = get_current_env()
        if env == "pre" and self._bcsfuse_config.base_url_pre:
            logger.info(
                f"[BotDiscover] pre 环境，使用 BCSFuse base_url_pre: {self._bcsfuse_config.base_url_pre}"
            )
            return self._bcsfuse_config.base_url_pre

        # base_url default carries the legacy hardcoded prod fallback
        # (see BcsFuseConfig docstring), so this always returns a URL.
        return self._bcsfuse_config.base_url

    def search_by_keyword(
        self,
        keyword: str,
        user_id: str | None = None,
        top_k: int = 10,
        min_score: float = 0.01,
        filters: Optional[dict[str, Any]] = None,
        catalog_filters: BotCatalogSearchFilters | None = None,
        caller: BotCatalogCaller | None = None,
        request_id: str = "internal-discover",
    ) -> dict[str, Any]:
        """根据关键词搜索公开的 Bot.

        1. 调用 BCSFuse 的 recommend 接口获取推荐 bot 列表
        2. 使用 BCS Catalog Search 过滤当前 viewer 不可见的 bot
        3. 从 BotRepository 查询完整展示信息（与 search 接口格式一致）

        Args:
            keyword: 搜索关键词
            user_id: 当前用户ID
            top_k: 返回结果数量（默认 10）
            min_score: 最小推荐分数（默认 0.01）
            filters: 过滤条件，默认 {"runtime_state": ["online"]}

        Returns:
            搜索结果字典，与 search 接口格式一致:
            {
                "total": int,
                "items": list[dict],  # 完整的 bot 信息列表，每个 item 包含 recommend 字段
            }
        """
        logger.info(f"[BotDiscover] 搜索关键词: {keyword}, top_k={top_k}, filters={filters}")

        # 1. 调用 BCSFuse recommend 接口
        bcsfuse_results = self._call_bcsfuse_recommend(
            question=keyword,
            top_k=top_k,
            min_score=min_score,
            filters=filters,
        )

        if not bcsfuse_results or not bcsfuse_results.get("recommendations"):
            logger.info("[BotDiscover] BCSFuse 返回空结果")
            return {"total": 0, "items": [], "context": {"recommend_response": bcsfuse_results}}

        recommendations = bcsfuse_results.get("recommendations", [])
        logger.info(f"[BotDiscover] BCSFuse 返回 {len(recommendations)} 个候选")

        # 2. Use the BCS catalog as the visibility authority, then join Backend details.
        if caller is None:
            caller = BotCatalogCaller(
                tenant_id=get_current_avernet_tenant(),
                user_id=user_id,
                app_id=None,
            )
        if catalog_filters is None:
            runtime_states = (filters or {}).get("runtime_state")
            status = (
                runtime_states[0]
                if isinstance(runtime_states, list)
                and len(runtime_states) == 1
                and runtime_states[0] in {"online", "hidden"}
                else None
            )
            catalog_filters = BotCatalogSearchFilters(
                status=status,
                viewer_actor_type="human" if user_id else None,
                viewer_actor_id=user_id,
            )
        result = self._get_public_bots_with_details(
            recommendations,
            catalog_filters=catalog_filters,
            caller=caller,
            request_id=request_id,
        )

        logger.info(
            f"[BotDiscover] 查询完成: {result['total']}/{len(recommendations)} 个 catalog-visible bot"
        )

        result["context"] = {"recommend_response": bcsfuse_results}
        return result

    def _call_bcsfuse_recommend(
        self,
        question: str,
        top_k: int,
        min_score: float,
        filters: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """调用 BCSFuse recommend 接口.

        Args:
            question: 问题/关键词
            top_k: 返回数量
            min_score: 最小分数
            filters: 过滤条件

        Returns:
            BCSFuse 响应字典，失败返回 None
        """
        url = f"{self._bcsfuse_base_url}/api/v1/recommend"

        payload = {
            "question": question,
            "topK": top_k,
            "min_score": min_score,
            "expand_factor": 10,
            "enable_rerank": True,
        }

        # 添加过滤条件
        if filters:
            payload["filters"] = filters

        try:
            logger.debug(f"[BotDiscover] 调用 BCSFuse: {url}")
            response = requests.post(
                url,
                json=payload,
                timeout=30,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            result = response.json()
            logger.debug(f"[BotDiscover] BCSFuse 响应: {result}")
            return result

        except requests.exceptions.Timeout:
            logger.error(f"[BotDiscover] BCSFuse 调用超时: {url}")
            return None
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[BotDiscover] BCSFuse 连接失败: {e}")
            return None
        except requests.exceptions.HTTPError as e:
            logger.error(f"[BotDiscover] BCSFuse HTTP 错误: {e}")
            return None
        except Exception as e:
            logger.error(f"[BotDiscover] BCSFuse 调用异常: {e}")
            return None

    def _get_public_bots_with_details(
        self,
        recommendations: list[dict[str, Any]],
        *,
        catalog_filters: BotCatalogSearchFilters,
        caller: BotCatalogCaller,
        request_id: str,
    ) -> dict[str, Any]:
        """获取 public bot 的完整信息.

        1. 解析 worker_id 获取 (bot_id, owner_id) 列表
        2. 通过 BCS 批量查询 Catalog 可见性
        3. 获取完整的 Backend bot 详情
        4. 组合返回结果

        Args:
            recommendations: BCSFuse 返回的推荐列表
            user_id: 当前用户ID

        Returns:
            包含 total, items 的字典
        """
        # 1. 收集所有需要验证的 bot
        bot_entries = []  # [(worker_id, bot_id, owner_id, rec), ...]
        for rec in recommendations:
            worker_id = rec.get("worker_id")
            if not worker_id:
                logger.debug(f"[BotDiscover] 跳过无 worker_id 的推荐: {rec}")
                continue

            bot_id, owner_id = self._parse_worker_id(worker_id)
            if not bot_id or not owner_id:
                logger.debug(f"[BotDiscover] 跳过格式错误的 worker_id: {worker_id}")
                continue

            bot_entries.append((worker_id, bot_id, owner_id, rec))

        if not bot_entries:
            return {"total": 0, "items": []}

        # 2. Ask BCS which exact recommendations are visible to this viewer.
        metadata_page = self._catalog_metadata_service.search_public_bot_metadata(
            search=None,
            page=1,
            page_size=len(bot_entries),
            bot_uuids=tuple(entry[0] for entry in bot_entries),
            filters=catalog_filters,
            caller=caller,
            request_id=request_id,
        )
        metadata_by_address = {
            (item.address.bot_id, item.address.entity_id): item
            for item in metadata_page.items
        }

        # 3. Join display details from Backend without consulting ac_bots.public.
        public_bot_details = self._batch_get_public_bots(
            bot_entries, metadata_by_address
        )

        # 4. 构建结果（每个 item 包含自己的 recommend 信息）
        items = []

        for bot_detail, rec in public_bot_details:
            # 将 recommend 信息合并到 bot 详情中
            bot_detail["recommend"] = {
                "profile_key": rec.get("profile_key"),
                "score": rec.get("score"),
                "reasons": rec.get("reasons", []),
                "short_profile": rec.get("short_profile"),
            }
            items.append(bot_detail)

        # 5. 按 recommend.score 降序排序
        items.sort(key=lambda x: x.get("recommend", {}).get("score", 0.0), reverse=True)

        # 6. Sanitize sensitive fields in ext
        self._sanitize_ext_fields(items)

        return {
            "total": len(items),
            "items": items,
        }

    def _parse_worker_id(self, worker_id: str) -> tuple[str | None, str | None]:
        """解析 worker_id 为 bot_id 和 owner_id.

        worker_id 格式: {bot_id}:{owner_id}

        Args:
            worker_id: Worker ID

        Returns:
            (bot_id, owner_id) 元组，解析失败返回 (None, None)
        """
        if not worker_id:
            return None, None

        # 找到最后一个冒号，因为 bot_id 本身可能包含冒号
        if ":" not in worker_id:
            return worker_id, None

        # 分离最后一部分作为 owner_id
        parts = worker_id.rsplit(":", 1)
        if len(parts) == 2:
            return parts[0], parts[1]

        return worker_id, None

    def _batch_get_public_bots(
        self,
        bot_entries: list[tuple[str, str, str, dict[str, Any]]],
        metadata_by_address: dict[tuple[str, str], BotCatalogMetadata],
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """批量获取已通过 BCS Catalog 过滤的 Bot 完整信息.

        使用单次 SQL 查询所有 bot，避免 N+1 问题。

        Args:
            bot_entries: [(worker_id, bot_id, owner_id, rec), ...]
            metadata_by_address: 已通过 BCS Catalog Search 的候选元数据

        Returns:
            [(bot_detail, rec), ...] 列表，只包含 BCS Catalog 可见的 bot
        """
        try:
            # 构建 (bot_id, owner_id) 条件列表
            pairs: list[tuple[str, str]] = []
            bot_id_to_rec = {}  # (bot_id, owner_id) -> (worker_id, rec)
            for worker_id, bot_id, owner_id, rec in bot_entries:
                if (bot_id, owner_id) not in metadata_by_address:
                    continue
                pairs.append((bot_id, owner_id))
                bot_id_to_rec[(bot_id, owner_id)] = (worker_id, rec)

            if not pairs:
                return []

            # ORM 查询（经 do_orm_execute tenant guard 覆盖，自动按 tenant 隔离）。
            # 曾经是对 ac_bots 的 raw cursor SQL —— raw SQL 绕过了 BotModel 的
            # tenant guard（listener 只覆盖 ORM statement），故改走仓储的 ORM 方法。
            _, bots = self._bot_repository.list_bots_by_owner_bot_pairs(
                pairs,
                page=1,
                page_size=len(pairs),
            )

            bots_by_address = {
                (bot["bot_id"], bot["owner_id"]): bot for bot in bots
            }
            result = []
            for _, bot_id, owner_id, _ in bot_entries:
                key = (bot_id, owner_id)
                bot = bots_by_address.get(key)
                if bot is None:
                    continue
                worker_id, rec = bot_id_to_rec.get(key, (None, None))
                if not rec:
                    continue
                metadata = metadata_by_address[key]

                # 构建完整的 bot 详情（与 search 接口格式一致，to_dict() 已解析 JSON）
                ext = bot.get("ext")
                bot_detail = {
                    "id": bot["id"],
                    "bot_id": bot["bot_id"],
                    "bot_type": bot["bot_type"],
                    "bot_name": bot["bot_name"],
                    "bot_desc": bot["bot_desc"],
                    "entity_id": bot["entity_id"],
                    "entity_type": bot["entity_type"],
                    "creator_id": bot["creator_id"],
                    "owner_id": bot["owner_id"],
                    "engine_types": bot["engine_types"],
                    "status": bot["status"],
                    "binding_id": bot["binding_id"],
                    "gmt_create": bot["gmt_create"],
                    "gmt_modified": bot["gmt_modified"],
                    "modifier_id": bot["modifier_id"],
                    "share_policy": bot["share_policy"],
                    "is_delete": bot["is_delete"],
                    "active_engine": bot["active_engine"],
                    "device_id": bot["device_id"],
                    "env": bot["env"],
                    "ext": ext,
                    "public": bot["public"],
                    "owner_name": (ext or {}).get("owner_name") if ext else None,
                    "bot_uuid": metadata.bot_uuid or worker_id,
                }
                for field_name in (
                    "is_friend",
                    "visibility",
                    "is_online",
                    "actor_kind",
                    "friend_ext",
                    "friend_check_in_strategy",
                    "user_visibility",
                ):
                    value = getattr(metadata, field_name)
                    if value is not None:
                        bot_detail[field_name] = value
                result.append((bot_detail, rec))

            logger.debug(
                f"[BotDiscover] 批量查询完成: {len(result)}/{len(bot_entries)} 个 catalog-visible"
            )
            return result

        except Exception as e:
            logger.warning(f"[BotDiscover] 批量获取 public bot 信息失败: {e}")
            return []

    @staticmethod
    def _sanitize_ext_fields(items: list[dict[str, Any]]) -> None:
        """Sanitize sensitive fields in bot items for public API responses.

        Redacts:
        - Top-level device_id
        - ext.passport.token
        - ext.iam_token

        Mutates items in place.
        """
        EXT_SENSITIVE_KEYS = {"iam_token"}
        for bot in items:
            # Redact top-level device_id
            if "device_id" in bot:
                bot["device_id"] = None
            # Redact sensitive fields inside ext
            ext = bot.get("ext")
            if isinstance(ext, str):
                try:
                    ext = _json.loads(ext)
                except _json.JSONDecodeError:
                    ext = {}
            if isinstance(ext, dict):
                if isinstance(ext.get("passport"), dict) and "token" in ext["passport"]:
                    ext["passport"]["token"] = None
                for key in EXT_SENSITIVE_KEYS:
                    if key in ext:
                        ext[key] = None
                bot["ext"] = ext
