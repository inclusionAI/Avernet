"""CapabilityVerifyService — 能力验证编排入口。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from src.domain.events.worker_profile_created_event import WorkerProfileCreatedEvent
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_task_spec import (
    Complexity,
    CostSensitivity,
    LLMTaskSpec,
    TaskType,
)
from src.domain.models.verify_dto import (
    DimensionJudgment,
    DimensionResult,
    PeerReviewResult,
    VerifyData,
)
from src.domain.models.worker import TrustLevel

if TYPE_CHECKING:
    from src.domain.services.adapters.worker_registry_store_adapter import (
        WorkerRegistryStoreAdapter,
    )
    from src.application.services.verify_prompt_composer import VerifyPromptComposer
    from src.application.services.verify_executor import VerifyExecutor
    from src.application.services.verify_judge import VerifyJudge
    from src.application.services.peer_review_service import PeerReviewService
    from src.infra.adapters.worker_profile_content_store import WorkerProfileContentStore

logger = logging.getLogger(__name__)

_DEFAULT_DEBUG_OUTPUT_DIR = "./data/capability_verify_debug"
_DEFAULT_PROFILE_ANALYSIS_POLL_INTERVAL = 2.0
_DEFAULT_PROFILE_ANALYSIS_MAX_WAIT = 30.0
_DEFAULT_GENERIC_CHECK_TIMEOUT_MS = 10000
_DEFAULT_GENERIC_CHECK_MAX_TOKENS = 256


class CapabilityVerifyService:
    """能力验证编排入口：监听事件 → 入队 → 消费协程验证 → 写入 trust_level。

    使用 asyncio.Queue + Consumer 模式替代 Semaphore，提供：
    - 背压：队列容量限制，满时丢弃并打日志
    - 去重：_pending 集合防止同一 worker_id 重复入队
    - 分布式幂等：CAS 操作 UNVERIFIED → VERIFYING 抢占验证权
    """

    def __init__(
        self,
        prompt_composer: VerifyPromptComposer,
        executor: VerifyExecutor,
        judge: VerifyJudge,
        worker_repo: WorkerRegistryStoreAdapter,
        profile_repo: WorkerProfileContentStore,
        peer_review_service: PeerReviewService | None = None,
        total_timeout: int = 300,
        debug_output_dir: str = "",
        profile_analysis_poll_interval: float = _DEFAULT_PROFILE_ANALYSIS_POLL_INTERVAL,
        profile_analysis_max_wait: float = _DEFAULT_PROFILE_ANALYSIS_MAX_WAIT,
        generic_check_timeout_ms: int = _DEFAULT_GENERIC_CHECK_TIMEOUT_MS,
        generic_check_max_tokens: int = _DEFAULT_GENERIC_CHECK_MAX_TOKENS,
        queue_max_size: int = 100,
        consumer_count: int = 2,
    ) -> None:
        self._prompt_composer = prompt_composer
        self._executor = executor
        self._judge = judge
        self._worker_repo = worker_repo
        self._profile_repo = profile_repo
        self._peer_review_service = peer_review_service
        self._total_timeout = total_timeout
        self._debug_output_dir = Path(debug_output_dir) if debug_output_dir else Path(_DEFAULT_DEBUG_OUTPUT_DIR)
        self._profile_analysis_poll_interval = profile_analysis_poll_interval
        self._profile_analysis_max_wait = profile_analysis_max_wait
        self._generic_check_timeout_ms = generic_check_timeout_ms
        self._generic_check_max_tokens = generic_check_max_tokens
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_max_size)
        self._pending: set[str] = set()
        self._consumer_count = consumer_count
        self._consumers: list[asyncio.Task] = []
        self._running = False

    @staticmethod
    def _is_generic_check_enabled() -> bool:
        """从 DRM 配置动态读取快速筛查开关，未配置时默认 True。"""
        from src.application.utils.drm_config_helper import is_generic_check_enabled
        drm_val = is_generic_check_enabled()
        return drm_val if drm_val is not None else True

    async def start(self) -> None:
        """启动消费协程。"""
        if self._running:
            return
        self._running = True
        for i in range(self._consumer_count):
            task = asyncio.create_task(self._consume_loop(i))
            self._consumers.append(task)
        logger.info(
            "CapabilityVerifyService: started %d consumers (queue_max_size=%d)",
            self._consumer_count,
            self._queue.maxsize,
        )

    async def stop(self) -> None:
        """优雅停止：发送 sentinel 通知 consumer 退出，等待清理。"""
        if not self._running:
            return
        self._running = False
        for _ in self._consumers:
            try:
                self._queue.put_nowait("")
            except asyncio.QueueFull:
                pass
        for t in self._consumers:
            try:
                await asyncio.wait_for(t, timeout=10)
            except asyncio.TimeoutError:
                t.cancel()
        self._consumers.clear()
        logger.info("CapabilityVerifyService: stopped all consumers")

    async def on_worker_profile_created(self, event: WorkerProfileCreatedEvent) -> None:
        """EventBus 回调：入队（带进程内去重和容量限制）。"""
        worker_id = event.worker_id
        if worker_id in self._pending:
            logger.info(
                "CapabilityVerifyService: worker_id=%s 已在队列中，跳过 (queue=%d/%d, pending=%d)",
                worker_id, self._queue.qsize(), self._queue.maxsize, len(self._pending),
            )
            return
        self._pending.add(worker_id)
        try:
            self._queue.put_nowait(worker_id)
            logger.info(
                "CapabilityVerifyService: worker_id=%s 入队 (queue=%d/%d, pending=%d)",
                worker_id, self._queue.qsize(), self._queue.maxsize, len(self._pending),
            )
        except asyncio.QueueFull:
            self._pending.discard(worker_id)
            logger.warning(
                "CapabilityVerifyService: 队列已满，丢弃 worker_id=%s (queue=%d/%d, pending=%d)",
                worker_id, self._queue.qsize(), self._queue.maxsize, len(self._pending),
            )

    async def _consume_loop(self, consumer_id: int) -> None:
        """消费协程主循环。"""
        while self._running:
            try:
                worker_id = await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            if not worker_id:
                break
            logger.info(
                "CapabilityVerifyService: consumer-%d 开始处理 worker_id=%s (queue=%d/%d, pending=%d)",
                consumer_id, worker_id, self._queue.qsize(), self._queue.maxsize, len(self._pending),
            )
            try:
                await asyncio.wait_for(
                    self._verify(WorkerProfileCreatedEvent(worker_id=worker_id)),
                    timeout=self._total_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("CapabilityVerifyService: 验证超时 worker_id=%s", worker_id)
                self._ensure_not_stuck(worker_id)
            except Exception:
                logger.exception("CapabilityVerifyService: 验证失败 worker_id=%s", worker_id)
                self._ensure_not_stuck(worker_id)
            finally:
                self._pending.discard(worker_id)
                self._queue.task_done()

    def _try_acquire_verify_lock(self, worker_id: str) -> bool:
        """CAS: UNVERIFIED → VERIFYING，返回是否抢占成功。

        利用存储层原子性保证分布式幂等：只有将 trust_level 从 UNVERIFIED
        更新为 VERIFYING 的实例才会执行验证。
        """
        try:
            worker = self._worker_repo.get_by_id(worker_id)
            if worker is None or worker.state.trust_level != TrustLevel.UNVERIFIED:
                return False
            self._worker_repo.update_trust_level(worker_id, TrustLevel.VERIFYING)
            logger.info("CapabilityVerifyService: CAS 成功 UNVERIFIED→VERIFYING worker_id=%s", worker_id)
            return True
        except Exception:
            logger.exception("CapabilityVerifyService: 获取验证锁失败 worker_id=%s", worker_id)
            return False

    def _ensure_not_stuck(self, worker_id: str) -> None:
        """超时/异常时，如果 worker 还卡在 VERIFYING，回退到 UNVERIFIED 以允许重试。"""
        try:
            worker = self._worker_repo.get_by_id(worker_id)
            if worker and worker.state.trust_level == TrustLevel.VERIFYING:
                self._worker_repo.update_trust_level(worker_id, TrustLevel.UNVERIFIED)
                logger.info("CapabilityVerifyService: VERIFYING→UNVERIFIED (允许重试) worker_id=%s", worker_id)
        except Exception:
            logger.exception("CapabilityVerifyService: 回退 VERIFYING 状态失败 worker_id=%s", worker_id)

    async def _verify(self, event: WorkerProfileCreatedEvent) -> None:
        worker = self._worker_repo.get_by_id(event.worker_id)
        if worker is None:
            logger.warning("CapabilityVerifyService: worker 不存在 %s", event.worker_id)
            return

        if not worker.capabilities:
            logger.info("CapabilityVerifyService: 零能力域，跳过 %s", event.worker_id)
            return

        # 分布式幂等：CAS UNVERIFIED → VERIFYING，抢占验证权
        if not self._try_acquire_verify_lock(event.worker_id):
            logger.info(
                "CapabilityVerifyService: 验证权已被其他实例抢占或状态非 UNVERIFIED，跳过 %s",
                event.worker_id,
            )
            return

        verify_data = await self._build_verify_data(worker)

        # 加载 profile 以获取 LLM 能力标签（用于 peer review 召回）
        profile = self._load_profile(worker)

        # BCN bot UUID: 优先使用 external_id（BCN 注册时的 bot_uuid），
        # 否则回退到 worker.id
        bot_uuid = worker.external_id or worker.id
        if bot_uuid != worker.id:
            logger.info(
                "CapabilityVerifyService: 使用 external_id 作为 BCN bot UUID: %s (worker_id=%s)",
                bot_uuid,
                worker.id,
            )

        # 第一轮：让 bot 自我介绍
        logger.info(
            "CapabilityVerifyService: 第一轮面试 - 自我介绍 worker_id=%s",
            event.worker_id,
        )
        bot_intro = await self._executor.send_intro(bot_uuid)
        if bot_intro:
            logger.info(
                "CapabilityVerifyService: Bot 自我介绍 (len=%d)",
                len(bot_intro),
            )
        else:
            logger.warning(
                "CapabilityVerifyService: Bot 自我介绍失败或为空 worker_id=%s",
                event.worker_id,
            )

        # 第二轮：基于自我介绍生成深度追问
        verify_data.bot_intro = bot_intro

        # ── 自我介绍快速筛查：纯标准 Skill bot 直接 sandbox ──
        generic_check = None
        if bot_intro and self._is_generic_check_enabled():
            generic_check = await self._check_generic_intro(bot_intro)
            if generic_check["is_generic"]:
                logger.info(
                    "CapabilityVerifyService: 自我介绍表明为纯标准 Skill bot，跳过面试 → SANDBOX_ONLY worker_id=%s",
                    event.worker_id,
                )
                self._write_debug_file(
                    event.worker_id,
                    bot_intro,
                    [],
                    [],
                    TrustLevel.SANDBOX_ONLY,
                    generic_check=generic_check,
                )
                self._worker_repo.update_trust_level(event.worker_id, TrustLevel.SANDBOX_ONLY)
                return

        # ── 优先尝试 peer review ──
        peer_results = None
        if self._peer_review_service is not None:
            try:
                llm_caps = []
                if profile and hasattr(profile, "contents") and isinstance(profile.contents, dict):
                    llm_caps = profile.contents.get("capabilities", []) or []

                peer_reviewers = await self._peer_review_service.find_peer_reviewers(
                    worker=worker,
                    llm_capabilities=llm_caps if isinstance(llm_caps, list) else None,
                    soul_md=verify_data.soul_md,
                )
                if peer_reviewers:
                    logger.info(
                        "CapabilityVerifyService: 找到 %d 个 peer reviewer，开始 peer review: %s",
                        len(peer_reviewers),
                        [(r.worker_id, r.similarity) for r in peer_reviewers],
                    )
                    peer_results = await self._peer_review_service.conduct_peer_review(
                        tested_bot_uuid=bot_uuid,
                        bot_intro=bot_intro,
                        verify_data=verify_data,
                        peer_reviewers=peer_reviewers,
                    )
                else:
                    logger.info("CapabilityVerifyService: 无满足阈值 peer reviewer，回退到 LLM judge 流程")
            except Exception:
                logger.exception("CapabilityVerifyService: peer review 异常，回退到 LLM judge 流程")

        if peer_results:
            # peer review 成功：合并 peer 评判与 LLM judge
            probes = await self._prompt_composer.compose(verify_data)
            results = await self._executor.execute(bot_uuid, probes, bot_intro=bot_intro)
            judgments = await self._judge.judge(verify_data, results)

            peer_judgments = self._peer_review_service.to_judgments(peer_results)
            trust_level = self._map_trust_level_with_peer(judgments, peer_judgments, peer_results)

            self._write_debug_file(
                event.worker_id,
                bot_intro,
                results,
                judgments,
                trust_level,
                peer_results=peer_results,
                generic_check=None,
            )
        else:
            # 回退到现有验证逻辑
            probes = await self._prompt_composer.compose(verify_data)
            results = await self._executor.execute(bot_uuid, probes, bot_intro=bot_intro)
            judgments = await self._judge.judge(verify_data, results)
            trust_level = self._map_trust_level(judgments)

            self._write_debug_file(
                event.worker_id,
                bot_intro,
                results,
                judgments,
                trust_level,
                generic_check=None,
            )

        self._worker_repo.update_trust_level(event.worker_id, trust_level)
        logger.info(
            "CapabilityVerifyService: 验证完成 worker_id=%s → %s",
            event.worker_id,
            trust_level.value,
        )

    async def _wait_for_profile_analysis(self, worker: "Worker") -> None:
        """等待 LLM 画像分析完成（contents["capabilities"] 被填充）。"""
        if not worker.active_profile_key:
            return

        _wid, _pid = worker.active_profile_key.rsplit(":", 1)
        elapsed = 0.0

        while elapsed < self._profile_analysis_max_wait:
            await asyncio.sleep(self._profile_analysis_poll_interval)
            elapsed += self._profile_analysis_poll_interval

            try:
                profile = self._profile_repo.get(_wid, _pid)
                if profile and hasattr(profile, "contents") and isinstance(profile.contents, dict):
                    caps = profile.contents.get("capabilities", [])
                    if caps:
                        logger.info(
                            "CapabilityVerifyService: LLM 画像分析完成 (等待 %.0fs), 标签数: %d",
                            elapsed,
                            len(caps),
                        )
                        return
            except Exception:
                pass

        logger.warning(
            "CapabilityVerifyService: 等待 LLM 画像分析超时 (%.0fs), 使用当前数据继续",
            elapsed,
        )

    async def _check_generic_intro(self, bot_intro: str) -> dict:
        """判断 bot 自我介绍是否表明其仅为标准 Skill 包装，无独有能力。

        返回 dict: {"is_generic": bool, "confidence": float, "reasoning": str}
        is_generic=True 时，直接置 SANDBOX_ONLY，跳过后续面试。
        confidence 表示判断为 generic 的置信度 (0.0-1.0)。
        """
        prompt = """
你是一个 bot 能力审查专家，负责做“第一轮快速筛查”。
你的任务是根据 bot 的自我介绍，判断它是否只是“平台默认能力的包装”，而不具备需要进一步验证的独有能力。

【你的目标】
识别出那些：
- 只是在复述 OpenClaw 平台默认工具、默认 skills、默认模型、默认插件或默认通道能力
- 没有提供任何“已实际配置/已实际拥有”的自定义能力证据
- 没有明确的私有工具、私有知识库、私有 workflow、私有 MCP server、专属数据源或专属任务闭环
这类 bot 应判定为 generic，直接归为 sandbox 级别。

【generic 的定义】
如果一个 bot 的介绍满足以下大多数特征，应判定为 generic=true：
1. 主要内容是在罗列平台内置工具、默认 skill、模型配置、上下文长度、频道、插件、CLI、MCP 管理能力等
2. 声称自己“支持扩展 / 支持 MCP / 可以添加工具 / 可以配置 workflow”，但没有说明已经实际配置了什么独有能力
3. 描述的是通用能力，如：读写文件、执行命令、联网搜索、浏览网页、发消息、多 agent 协作、定时任务、记忆检索等
4. 没有给出可区分于其他默认 bot 的专属资源或专属能力证据
5. 所谓“专家”“助手”“顾问”等角色定位只是营销性包装，没有对应的专有知识、专有工具或稳定工作流支撑

【判定为 non-generic 的强信号】
如果 bot 自我介绍中明确出现以下任一类“已拥有、已接入、已配置、已训练、已沉淀”的独有能力证据，应倾向判定 generic=false：
1. 自定义 skill / 私有 skill / 非平台默认工具
2. 已接入并可调用的私有 MCP server、外部 API、内部系统、数据库、知识库、CRM、工单系统等
3. 明确的专属 workflow、自动化流程、行业 SOP、业务闭环
4. 明确限定且可信的领域专长，并伴随专属数据、规则、知识源或长期记忆沉淀
5. 能完成某个非通用任务，且该能力明显不是仅靠平台默认工具清单就能推出的

【重要区分规则】
- “支持/可接入/可配置/可扩展” 不等于 “已经具备”
- “有 MCP 管理能力” 不等于 “已配置了私有 MCP 工具”
- “可以连接数据库” 不等于 “已经拥有数据库能力”
- “我是专家/顾问/助手” 不等于 “真的具备该领域独有能力”
- 如果介绍里主要是平台说明书、能力菜单、工具列表、默认 skills 列表，应优先判定为 generic
- 即使文案写得很强大，只要本质上都是平台默认能力，也应判定为 generic

【平台默认能力提示】
以下内容默认不构成差异化价值：
- OpenClaw 平台内置工具与系统基础工具，如 read / write / edit / exec / browser / search / web_fetch / message / cron / sessions / subagents / memory 等
- 平台默认 skill 或官方标准 skill，如 bcs-coordination、bot-soul-manage、web-search-asap、teamclaw-cli 等
- 默认模型、上下文窗口、插件、频道、节点、网关、可观测、安全守护等平台基础设施能力
- “可管理 MCP / 可添加 MCP server / 支持 stdio 或 HTTP” 这类平台扩展能力本身

【判定原则】
- 这是一次保守筛查：只有在看到了明确的独有能力证据时，才判定 generic=false
- 若 bot 只是泛泛声称自己很专业、很强大、很全面，但缺乏独特能力证据，判定为 generic=true
- 若信息不足，但看起来只是平台默认能力汇总，也判定为 generic=true
- 不要被夸张措辞迷惑，重点看“是否存在已实际拥有的差异化能力证据”


请严格按以下 JSON 格式输出，不要输出任何其他内容：
{{"is_generic": true/false, "confidence": 0.0-1.0 (置信度), "reasoning": "简短说明判断依据"}}


"""

        try:
            request = LLMRequest(
                task_spec=LLMTaskSpec(
                    task_type=TaskType.RATIONALE_GENERATION,
                    complexity=Complexity.LOW,
                    need_structured_output=True,
                    cost_sensitivity=CostSensitivity.MEDIUM,
                    latency_budget_ms=self._generic_check_timeout_ms,
                ),
                system_prompt=prompt,
                user_prompt=f"Bot 自我介绍：\n{bot_intro[:3000]}",
                temperature=0.1,
                max_tokens=self._generic_check_max_tokens,
            )
            response = await asyncio.to_thread(self._judge._llm.generate, request)
            raw = response.raw_text.strip() if response.raw_text else ""

            if not raw:
                logger.warning("CapabilityVerifyService: 自我介绍筛查 LLM 返回为空，跳过筛查")
                return {"is_generic": False, "confidence": 0.0, "reasoning": "LLM 返回为空"}

            text = raw
            fence_start = text.find("{")
            fence_end = text.rfind("}")
            if fence_start != -1 and fence_end != -1:
                text = text[fence_start : fence_end + 1]

            data = json.loads(text)
            is_generic = bool(data.get("is_generic", False))
            confidence = float(data.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            reasoning = str(data.get("reasoning", ""))

            logger.info(
                "CapabilityVerifyService: 自我介绍判断结果: is_generic=%s, confidence=%.3f, reasoning=%s",
                is_generic,
                confidence,
                reasoning[:300]
            )

            return {"is_generic": is_generic, "confidence": confidence, "reasoning": reasoning}

        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning(
                "CapabilityVerifyService: 自我介绍判断解析失败，跳过筛查: %s",
                raw[:200] if raw else "",
            )
            return {"is_generic": False, "confidence": 0.0, "reasoning": f"JSON 解析失败: {raw[:200] if raw else ''}"}
        except Exception:
            logger.exception("CapabilityVerifyService: 自我介绍筛查异常，跳过筛查")
            return {"is_generic": False, "confidence": 0.0, "reasoning": "筛查异常"}

    async def _build_verify_data(self, worker: "Worker") -> VerifyData:
        from src.domain.models.worker import Capability, CapabilityLevel

        soul_md = ""
        skill_sets: list[dict] = []
        llm_capabilities: list[str] = []

        # 判断 worker.capabilities 是否仅为默认的 "general"
        is_only_general = (
            len(worker.capabilities) == 1
            and worker.capabilities[0].name.lower() == "general"
        )

        # 如果是默认 general，等待 LLM 画像分析完成以获取真实能力标签
        if is_only_general:
            logger.info(
                "CapabilityVerifyService: capabilities 为默认 general，等待 LLM 画像分析..."
            )
            await self._wait_for_profile_analysis(worker)

        # 读取 profile（此时 LLM 分析结果应该已写入）
        profile = self._load_profile(worker)
        if profile:
            soul_md = getattr(profile, "soul_md", "") or ""
            raw_skill_sets = getattr(profile, "skill_sets", []) or []
            skill_sets = [
                s.model_dump() if hasattr(s, "model_dump") else s
                for s in raw_skill_sets
            ]
            if hasattr(profile, "contents") and isinstance(profile.contents, dict):
                llm_capabilities = profile.contents.get("capabilities", []) or []

        # 如果 soul_md 为空，用 identity.description 兜底
        if not soul_md and worker.identity.description:
            soul_md = worker.identity.description
            logger.info("CapabilityVerifyService: 使用 identity.description 兜底 soul_md")

        # 同时读取 LLM 生成的语义画像（contents["profile"]），补充到 soul_md
        llm_profile = ""
        if profile and hasattr(profile, "contents") and isinstance(profile.contents, dict):
            llm_profile = profile.contents.get("profile", "") or ""

        # 合并 soul_md 和 LLM 画像
        if llm_profile and soul_md:
            soul_md = f"{soul_md}\n\n{llm_profile}"
        elif llm_profile:
            soul_md = llm_profile

        if is_only_general and llm_capabilities:
            caps = [
                Capability(name=tag, level=CapabilityLevel.INTERMEDIATE)
                for tag in llm_capabilities
                if isinstance(tag, str)
            ]
        elif is_only_general and soul_md:
            caps = worker.capabilities
        else:
            caps = worker.capabilities

        return VerifyData(
            worker_id=worker.id,
            capabilities=caps,
            soul_md=soul_md,
            skill_sets=skill_sets,
        )

    def _load_profile(self, worker: "Worker") -> object | None:
        if not worker.active_profile_key:
            logger.warning(
                "CapabilityVerifyService: worker %s 无 active_profile_key，画像数据为空",
                worker.id,
            )
            return None
        try:
            _wid, _pid = worker.active_profile_key.rsplit(":", 1)
            logger.info(
                "CapabilityVerifyService: 读取 profile worker_id=%s profile_id=%s",
                _wid,
                _pid,
            )
            profile = self._profile_repo.get(_wid, _pid)
            if profile:
                logger.debug(
                    "CapabilityVerifyService: profile 加载成功 soul_md_len=%d skill_sets_count=%d contents_keys=%s",
                    len(getattr(profile, "soul_md", "") or ""),
                    len(getattr(profile, "skill_sets", []) or []),
                    list(getattr(profile, "contents", {}).keys()) if hasattr(profile, "contents") else [],
                )
            else:
                logger.warning(
                    "CapabilityVerifyService: profile 不存在 worker_id=%s profile_id=%s",
                    _wid,
                    _pid,
                )
            return profile
        except Exception:
            logger.exception("CapabilityVerifyService: 读取 profile 失败")
            return None

    @staticmethod
    def _get_trust_thresholds() -> tuple[float, float]:
        """从 DRM 配置获取信任等级阈值，回退到默认值 (0.8, 0.6)。"""
        from src.application.utils.drm_config_helper import get_trust_level_thresholds
        thresholds = get_trust_level_thresholds()
        if thresholds:
            try:
                trusted = float(thresholds.get("trusted", 0.8))
                guarded = float(thresholds.get("guarded", 0.6))
                return (trusted, guarded)
            except (ValueError, TypeError):
                logger.warning("Invalid trust level thresholds from DRM config, using defaults")
        return (0.8, 0.6)

    def _map_trust_level(self, judgments: list[DimensionJudgment]) -> TrustLevel:
        if not judgments:
            return TrustLevel.SANDBOX_ONLY

        cap_scores: dict[str, list[float]] = defaultdict(list)
        for j in judgments:
            cap_scores[j.capability_name].append(j.confidence)

        def trim_mean(scores: list[float]) -> float:
            """去掉一个最高分和一个最低分后取平均（不足3个则全量取平均）。"""
            if len(scores) <= 2:
                return sum(scores) / len(scores)
            trimmed = sorted(scores)[1:-1]
            return sum(trimmed) / len(trimmed)

        cap_means = [trim_mean(scores) for scores in cap_scores.values()]
        overall = sum(cap_means) / len(cap_means)

        trusted_threshold, guarded_threshold = self._get_trust_thresholds()
        if overall >= trusted_threshold:
            return TrustLevel.TRUSTED
        if overall >= guarded_threshold:
            return TrustLevel.GUARDED
        return TrustLevel.SANDBOX_ONLY

    def _map_trust_level_with_peer(
        self,
        judgments: list[DimensionJudgment],
        peer_judgments: list[tuple[str, float, str]],
        peer_results: list,
    ) -> TrustLevel:
        """合并 LLM judge 和 peer review 结果计算 trust level。

        权重策略：peer review 权重 0.5，LLM judge 权重 0.5。
        如果 peer review 有多个 reviewer，取平均。
        """
        # LLM judge 评分
        if judgments:
            cap_scores: dict[str, list[float]] = defaultdict(list)
            for j in judgments:
                cap_scores[j.capability_name].append(j.confidence)
            cap_means = [sum(scores) / len(scores) for scores in cap_scores.values()]
            llm_score = sum(cap_means) / len(cap_means)
        else:
            llm_score = 0.0

        # Peer review 评分（过滤 overall_confidence=0 的无效 peer）
        valid_peer_judgments = [p for p in peer_judgments if p[1] > 0]
        if valid_peer_judgments:
            peer_score = sum(p[1] for p in valid_peer_judgments) / len(valid_peer_judgments)
            overall = 0.5 * llm_score + 0.5 * peer_score
        else:
            peer_score = 0.0
            overall = llm_score

        logger.info(
            "CapabilityVerifyService: peer review 合并评分 llm=%.3f peer=%.3f overall=%.3f (valid_peers=%d/%d)",
            llm_score,
            peer_score,
            overall,
            len(valid_peer_judgments),
            len(peer_judgments),
        )

        trusted_threshold, guarded_threshold = self._get_trust_thresholds()
        if overall >= trusted_threshold:
            return TrustLevel.TRUSTED
        if overall >= guarded_threshold:
            return TrustLevel.GUARDED
        return TrustLevel.SANDBOX_ONLY

    def _write_debug_file(
        self,
        worker_id: str,
        bot_intro: str,
        results: list[DimensionResult],
        judgments: list[DimensionJudgment],
        trust_level: TrustLevel,
        peer_results: list | None = None,
        generic_check: dict | None = None,
    ) -> None:
        try:
            self._debug_output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_worker_id = worker_id.replace(":", "_").replace("/", "_")
            filepath = self._debug_output_dir / f"{safe_worker_id}_{timestamp}.json"

            judgment_by_key: dict[str, DimensionJudgment] = {
                f"{j.capability_name}/{j.dimension}": j for j in judgments
            }

            probe_records: list[dict] = []
            for r in results:
                key = f"{r.capability_name}/{r.dimension}"
                j = judgment_by_key.get(key)
                record: dict = {
                    "capability_name": r.capability_name,
                    "dimension": r.dimension,
                    "interview_question": r.probe_prompt,
                    "bot_answer": r.response_content,
                    "failed": r.failed,
                }
                if j:
                    record["judgment"] = {
                        "confidence": j.confidence,
                        "reasoning": j.reasoning,
                    }
                probe_records.append(record)

            cap_scores: dict[str, list[float]] = defaultdict(list)
            for j in judgments:
                cap_scores[j.capability_name].append(j.confidence)
            cap_summary = {
                cap: round(sum(scores) / len(scores), 3)
                for cap, scores in cap_scores.items()
            }

            cap_means_vals = [sum(s) / len(s) for s in cap_scores.values()] if cap_scores else [0.0]
            overall_confidence = round(sum(cap_means_vals) / len(cap_means_vals), 3)

            debug_data: dict = {
                "conclusion": f"验证完成 worker_id={worker_id} → {trust_level.value} (overall_confidence={overall_confidence})",
                "worker_id": worker_id,
                "trust_level": trust_level.value,
                "overall_confidence": overall_confidence,
                "timestamp": datetime.now().isoformat(),
                "overall_summary": {
                    "total_capacity": len(results),
                    "total_judgments": len(judgments),
                    "capability_means": cap_summary,
                },
                "bot_introduction": bot_intro,
                "capacity_list": probe_records,
            }

            if generic_check:
                debug_data["generic_intro_check"] = {
                    "is_generic": generic_check.get("is_generic", False),
                    "confidence": generic_check.get("confidence", 0.0),
                    "reasoning": generic_check.get("reasoning", ""),
                }

            if peer_results:
                peer_data = []
                for pr in peer_results:
                    peer_data.append({
                        "peer_worker_id": pr.peer_worker_id,
                        "peer_bot_uuid": pr.peer_bot_uuid,
                        "similarity": pr.similarity,
                        "overall_confidence": pr.overall_confidence,
                        "reasoning": pr.reasoning,
                        "items": [
                            {
                                "question": item.question,
                                "tested_bot_answer": item.tested_bot_answer[:500] if item.tested_bot_answer else "",
                                "peer_evaluation": item.peer_evaluation,
                                "confidence": item.confidence,
                            }
                            for item in pr.items
                        ],
                    })
                debug_data["peer_review"] = peer_data

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(debug_data, f, ensure_ascii=False, indent=2)
            logger.info(
                "CapabilityVerifyService: Debug output written to %s (%d probes, trust=%s, peer_review=%s, generic_check=%s)",
                filepath,
                len(probe_records),
                trust_level.value,
                "yes" if peer_results else "no",
                "yes" if generic_check else "no",
            )
        except Exception:
            logger.exception("CapabilityVerifyService: Failed to write debug file")