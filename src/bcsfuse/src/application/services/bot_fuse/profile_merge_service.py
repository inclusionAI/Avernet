"""
ProfileMergeService

G9: Profile Merge 模式

基于原始 MD 文件的 Profile 融合服务（简化版）。

核心流程：
1. 收集原始 Profile 内容
2. 简单 MD5 去重（相同内容跳过）
3. 一次性调用 LLM 生成超级 BOT Profile（6 项输出）
4. 超级 BOT Profile 作为后续对话的 System Prompt

只融合：
- soul.md, identity.md, memory.md, name, description, skills

不融合：
- agents.md, tools.md 及其他扩展文件

存储策略：
- 必须注入 FusedProfileStorageService（包含 L1 内存缓存 + L2 持久化）
- G9 模式 fusion_id 基于内容哈希，相同内容可复用缓存
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING

from src.domain.enums.fuse_enums import FusionMode
from src.domain.models.profile_fusion import FusedProfile, ExpertProfile
from src.domain.models.profile_fusion.fused_profile import ProfileFusionResult
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType, Complexity, CostSensitivity
from src.domain.models.worker_profile_content import WorkerProfileContent

if TYPE_CHECKING:
    from src.domain.services.adapters.worker_profile_content_store_adapter import WorkerProfileContentStoreAdapter
    from src.application.services.llm_gateway_service import LLMGatewayService
    from application.services.bot_fuse.fused_profile_storage_service import FusedProfileStorageService
    from src.domain.models.profile_fusion import FusionContext

logger = logging.getLogger(__name__)


# LLM 融合 Prompt 模板
FUSION_PROMPT_TEMPLATE = """请将以下 {count} 位专家的能力融合为一个超级 BOT Profile。

【专家信息】
{experts_info}

【输出格式】

输出一个 JSON 对象，包含以下字段：

- name: 字符串，超级BOT名称
- description: 字符串，整体角色概述，说明团队组成和核心能力
- persona: 字符串，人格画像，必须按专家分段展示每位专家的专业信息
- memory: 字符串，经验知识，保留各专家的关键经验和最佳实践
- skills: 字符串数组，合并去重后的技能列表

【JSON 结构】

{{
  "name": "<团队名称>",
  "description": "<整体概述>",
  "persona": "<详细人格画像>",
  "memory": "<经验知识>",
  "skills": ["<技能1>", "<技能2>"]
}}

【persona 字段结构要求】

persona 是最重要的字段，必须包含两部分：各专家独立段落和融合视角总结。

**第一部分：各专家独立段落**

为每位专家创建独立段落，格式如下：

## 专家N：<专家名称>（<角色/职位>）

### 角色定位
<该专家的核心身份、职业定位、价值主张>

### 专业背景
- 经验年限：<根据专家信息填写>
- 专业领域：<擅长领域列表>
- 技术栈：<核心技术栈>
- 关键能力：<核心竞争力>

### 专业视角
<该专家解决问题的方式、思考框架、决策偏好>

---

<重复上述结构，直到所有专家都完整呈现>

**第二部分：融合视角总结**

在所有专家分段后，必须添加融合视角章节：

## 团队融合视角

作为多专家协作团队，我们具备以下融合优势：

### 视角交叉
<描述不同专家视角如何交叉互补，形成更全面的分析能力>

### 综合优势
<总结团队相比单一专家的独特优势，如跨领域协同能力>

### 创新思路
<不同专业背景碰撞可能产生的新视角和创新方案>

【数据处理规则】

1. persona 字段：
   - 必须为【专家信息】中的每位专家创建完整段落
   - 必须在专家分段后添加"团队融合视角"章节
   - 内容必须基于【专家信息】中的原始数据，不得编造
   - 保留专家的原始名称和角色信息
   - 使用 Markdown 格式，包含标题层级

2. memory 字段：
   - 按【专家信息】中的内容提取关键经验
   - 可按领域或专家组织内容
   - 保留原始经验中的具体方法、案例细节

3. skills 字段：
   - 从【专家信息】中提取并合并
   - 相似技能合并，如"Java开发"和"Java编程"合并为"Java开发"
   - 按重要性排序

【输出要求】

- 输出必须是合法 JSON
- JSON 字符串值中可包含 Markdown 格式（换行用\\n表示）
- 不得输出任何 JSON 以外的内容
- 使用中文输出"""


class ProfileMergeService:
    """
    Profile 融合服务（G9 模式）

    输出格式（固定 6 项）：
    - name: 超级 BOT 名称
    - description: 超级 BOT 描述
    - soul: 核心身份定位
    - identity: 身份信息
    - memory: 经验知识
    - skills: 技能集

    存储策略：
    - 必须注入 FusedProfileStorageService（包含 L1 内存缓存 + L2 持久化）
    - G9 模式 fusion_id 基于内容哈希，相同内容可复用缓存
    """

    def __init__(
        self,
        profile_store: "WorkerProfileContentStoreAdapter",
        llm_gateway: "LLMGatewayService",
        storage_service: "FusedProfileStorageService",
        llm_executor: Optional[ThreadPoolExecutor] = None,
    ):
        """
        初始化服务

        Args:
            profile_store: Profile 内容存储适配器
            llm_gateway: LLM Gateway 服务
            storage_service: 存储服务（包含 L1 内存缓存 + L2 持久化）
            llm_executor: LLM 线程池（可选，用于并发控制）
        """
        self._store = profile_store
        self._llm_gateway = llm_gateway
        self._storage_service = storage_service
        self._llm_executor = llm_executor
        logger.info("[ProfileFusion] 使用 FusedProfileStorageService 存储服务")

    def _check_llm_queue_available(self) -> bool:
        """
        检查 LLM 线程池队列是否有空位

        Returns:
            bool: True 表示可以提交任务，False 表示队列有等待任务（快速失败）
        """
        if self._llm_executor is None:
            # 没有传入线程池，不做并发控制
            return True
        # 获取线程池内部队列长度
        queue_size = self._llm_executor._work_queue.qsize()
        return queue_size == 0

    def set_llm_executor(self, executor: ThreadPoolExecutor) -> None:
        """
        设置 LLM 线程池（用于并发控制）

        由外部（如 GroupFusionService）在创建线程池后调用，
        实现线程池共享。

        Args:
            executor: ThreadPoolExecutor 实例
        """
        self._llm_executor = executor
        logger.info("[ProfileFusion] 已设置共享 LLM 线程池")

    def fuse_profiles(
        self,
        ctx: "FusionContext",
    ) -> ProfileFusionResult:
        """
        融合多个 participant 的 Profile

        Args:
            ctx: 融合上下文，包含所有必要参数

        Returns:
            ProfileFusionResult: 融合结果
        """
        start_time = time.time()
        fusion_id = ctx.fusion_id
        profiles = ctx.profiles
        participant_ids = ctx.participant_ids
        refresh = ctx.refresh

        logger.info("[ProfileFusion] ========== 开始融合 ==========")
        logger.info("[ProfileFusion] participant_ids: %s, refresh: %s, fusion_id: %s", participant_ids, refresh, fusion_id)

        warnings: list[str] = []
        errors: list[str] = []
        logger.info("[ProfileFusion] 使用外部传入的 profiles: %d 个", len(profiles))

        # 统计 Profile 内容大小
        total_profile_size = 0
        for i, p in enumerate(profiles):
            profile_size = 0
            if p.soul_md:
                profile_size += len(p.soul_md)
            for content in p.contents.values() if p.contents else []:
                if isinstance(content, str):
                    profile_size += len(content)
            total_profile_size += profile_size
            logger.debug("[ProfileFusion] Profile[%d]: worker_id=%s, size=%d chars",
                        i, p.worker_id, profile_size)
        logger.info("[ProfileFusion] 总 Profile 大小: %d chars", total_profile_size)

        # 空边界检查
        if not profiles:
            return ProfileFusionResult(
                fusion_id=fusion_id,
                fused_profile=FusedProfile(
                    fused_profile_id=fusion_id,
                    source_participants=[],
                ),
                individual_profiles=[],
                warnings=warnings,
                errors=errors,
            )

        # 缓存查询
        step2_start = time.time()
        if refresh:
            logger.info("[ProfileFusion] 缓存: refresh=True, 跳过缓存查询")
        else:
            existing = self._storage_service.find_by_fusion_id(fusion_id)
            if existing and existing.fuse_detail:
                step2_elapsed = time.time() - step2_start
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info("[ProfileFusion] 缓存命中: fusion_id=%s, 耗时=%.3fs", fusion_id, step2_elapsed)
                # 从 fuse_detail 恢复 FusedProfile
                cached_profile = self._restore_fused_profile(existing.fuse_detail, profiles)
                return ProfileFusionResult(
                    fusion_id=fusion_id,
                    fused_profile=cached_profile,
                    individual_profiles=participant_ids,
                    warnings=warnings + ["from_storage"],
                    errors=errors,
                    cache_hit=True,
                    fusion_timing_ms=elapsed_ms,
                )
            else:
                logger.info("[ProfileFusion] 缓存未命中: fusion_id=%s", fusion_id)

        # Step 3: MD5 去重，收集不同内容
        step3_start = time.time()
        collected, dedup_count = self._collect_and_dedup(profiles)
        step3_elapsed = time.time() - step3_start
        logger.info("[ProfileFusion] Step-去重: 耗时=%.3fs, 跳过=%d 个重复", step3_elapsed, dedup_count)

        # Step 4: 一次性调用 LLM 生成超级 BOT Profile
        step4_start = time.time()
        fused_profile = self._llm_generate_super_agent(profiles, collected)
        fused_profile.fused_profile_id = fusion_id
        fused_profile.dedup_count = dedup_count
        step4_elapsed = time.time() - step4_start

        # Step 5: 写入存储（包含 L1 内存缓存 + L2 持久化）
        step5_start = time.time()
        self._storage_service.save_fused_profile(
            fusion_id=fusion_id,
            fusion_mode=FusionMode.BOT_PROFILE_FUSE,
            participant_ids=participant_ids,
            fuse_detail=fused_profile.model_dump(),
            profiles=ctx.profiles_dict,
            group_id=ctx.group_id,
            driver_bot_id=ctx.driver_bot_id,
            question=ctx.question,
            created_by=ctx.driver_bot_id,
            refresh=refresh,
        )
        logger.info("[ProfileFusion] Step-存储: fusion_id=%s", fusion_id)
        step5_elapsed = time.time() - step5_start

        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info("[ProfileFusion] ========== 融合完成 ==========")
        logger.info("[ProfileFusion] 总耗时: %dms", elapsed_ms)

        return ProfileFusionResult(
            fusion_id=fusion_id,
            fused_profile=fused_profile,
            individual_profiles=participant_ids,
            warnings=warnings,
            errors=errors,
            cache_hit=False,
            fusion_timing_ms=elapsed_ms,
        )

    def collect_profiles(
        self,
        participant_ids: list[str],
    ) -> tuple[list[WorkerProfileContent], list[str], list[str]]:
        """
        收集原始 Profile（公开方法，供上层调用）

        Args:
            participant_ids: Participant ID 列表，每个值直接作为 worker_id 使用

        Returns:
            (profiles, warnings, errors)
        """
        return self._collect_profiles(participant_ids)

    def _collect_profiles(
        self,
        participant_ids: list[str],
    ) -> tuple[list[WorkerProfileContent], list[str], list[str]]:
        """收集原始 Profile（内部方法）

        Args:
            participant_ids: Participant ID 列表，每个值直接作为 worker_id 使用

        Returns:
            (profiles, warnings, errors)
        """
        profiles = []
        warnings = []
        errors = []

        for worker_id in participant_ids:
            try:
                # participant_id 直接作为 worker_id 使用
                profile = self._store.get_active(worker_id)
                if profile:
                    profiles.append(profile)
                    logger.debug("[ProfileFusion] 获取 Profile 成功: worker_id=%s", worker_id)
                else:
                    warnings.append(f"Worker {worker_id} profile not found")
                    logger.warning("[ProfileFusion] Profile 不存在: worker_id=%s", worker_id)
            except Exception as e:
                errors.append(f"获取 Profile 失败: worker_id={worker_id}, error={str(e)}")
                logger.error("[ProfileFusion] 获取 Profile 异常: worker_id=%s, error=%s", worker_id, str(e))

        return profiles, warnings, errors

    def _compute_md5(self, content: str) -> str:
        """计算内容的 MD5 哈希"""
        if not content:
            return ""
        normalized = content.strip().replace('\r\n', '\n').replace('\r', '\n')
        return hashlib.md5(normalized.encode('utf-8')).hexdigest()

    def _collect_and_dedup(
        self,
        profiles: list[WorkerProfileContent],
    ) -> tuple[dict, int]:
        """
        收集内容并用 MD5 去重

        Returns:
            (collected, dedup_count)
            - collected: {field: [(pid, content), ...]} 去重后的内容
            - dedup_count: 跳过的重复内容数量
        """
        collected = defaultdict(list)
        seen_hashes = defaultdict(set)  # {field: set(md5)}
        dedup_count = 0

        for profile in profiles:
            pid = profile.worker_id

            # 收集 soul.md
            if profile.soul_md:
                md5 = self._compute_md5(profile.soul_md)
                if md5 not in seen_hashes["soul"]:
                    seen_hashes["soul"].add(md5)
                    collected["soul"].append((pid, profile.soul_md))
                else:
                    dedup_count += 1

            # 收集 identity.md
            identity = profile.contents.get("identity.md")
            if identity:
                md5 = self._compute_md5(identity)
                if md5 not in seen_hashes["identity"]:
                    seen_hashes["identity"].add(md5)
                    collected["identity"].append((pid, identity))
                else:
                    dedup_count += 1

            # 收集 memory.md
            memory = profile.contents.get("memory.md")
            if memory:
                md5 = self._compute_md5(memory)
                if md5 not in seen_hashes["memory"]:
                    seen_hashes["memory"].add(md5)
                    collected["memory"].append((pid, memory))
                else:
                    dedup_count += 1

            # 收集 description
            if profile.description:
                md5 = self._compute_md5(profile.description)
                if md5 not in seen_hashes["description"]:
                    seen_hashes["description"].add(md5)
                    collected["description"].append((pid, profile.description))
                else:
                    dedup_count += 1

            # 收集 name (display_name)
            if profile.display_name:
                md5 = self._compute_md5(profile.display_name)
                if md5 not in seen_hashes["name"]:
                    seen_hashes["name"].add(md5)
                    collected["name"].append((pid, profile.display_name))
                else:
                    dedup_count += 1

        return collected, dedup_count

    def _llm_generate_super_agent(
        self,
        profiles: list[WorkerProfileContent],
        collected: dict,
    ) -> FusedProfile:
        """一次性调用 LLM 生成超级 BOT Profile（6 个字段）"""
        llm_start = time.time()
        logger.info("[ProfileFusion] Step4-LLM: ========== 开始生成超级 BOT Profile ==========")

        # 构建专家信息
        experts_info = self._build_experts_info(profiles, collected)
        experts_info_size = len(experts_info)
        logger.info("[ProfileFusion] Step4-LLM: experts_info 大小: %d chars (%.1f KB)",
                   experts_info_size, experts_info_size / 1024)

        # 构建 Prompt
        prompt = FUSION_PROMPT_TEMPLATE.format(
            count=len(profiles),
            experts_info=experts_info,
        )
        prompt_size = len(prompt)
        logger.info("[ProfileFusion] Step4-LLM: user_prompt 大小: %d chars (%.1f KB)",
                   prompt_size, prompt_size / 1024)

        # 估算 token 数（粗略估计：中文约 1.5 字/token，英文约 4 字/token）
        estimated_tokens = prompt_size // 3  # 取中间值
        logger.info("[ProfileFusion] Step4-LLM: 估算输入 token 数: ~%d tokens", estimated_tokens)

        # Prompt 预览（前 500 字符）
        prompt_preview = prompt[:500] if len(prompt) > 500 else prompt
        logger.info("[ProfileFusion] Step4-LLM: prompt 预览 (前500字符):\n%s...", prompt_preview)

        # 调用 LLM
        task_spec = LLMTaskSpec(
            task_type=TaskType.PROFILE_FUSION,
            complexity=Complexity.HIGH,
            need_structured_output=True,
            cost_sensitivity=CostSensitivity.LOW,
            latency_budget_ms=60000,
        )

        system_prompt = "你是一个专业的专家能力整合助手，擅长将多人的能力信息融合为一个统一的超级 BOT Profile。请严格按照 JSON 格式输出。"
        system_prompt_size = len(system_prompt)
        total_prompt_size = system_prompt_size + prompt_size

        request = LLMRequest(
            task_spec=task_spec,
            system_prompt=system_prompt,
            user_prompt=prompt,
            temperature=0.3,
            max_tokens=4096,
        )

        logger.info("[ProfileFusion] Step4-LLM: 调用 LLM Gateway...")
        logger.info("[ProfileFusion] Step4-LLM: system_prompt=%d chars, user_prompt=%d chars, total=%d chars (%.1f KB)",
                   system_prompt_size, prompt_size, total_prompt_size, total_prompt_size / 1024)
        logger.info("[ProfileFusion] Step4-LLM: max_tokens=4096, temperature=0.3, latency_budget_ms=60000")
        # 完整打印 prompt 用于调试
        logger.info("[ProfileFusion] Step4-LLM: system_prompt 完整内容:\n%s", system_prompt)
        logger.info("[ProfileFusion] Step4-LLM: user_prompt 完整内容:\n%s", prompt)

        # 检查队列是否有等待任务（如果传入了线程池）
        if not self._check_llm_queue_available():
            llm_elapsed = time.time() - llm_start
            error_msg = "LLM 服务当前负载过高，请稍后重试。如问题持续，请联系开发人员增加资源配置。"
            logger.warning("[ProfileFusion] Step4-LLM: %s", error_msg)
            result = self._fallback_fusion(profiles, collected)
            result.fusion_strategy = "fallback_queue_full"
            return result

        try:
            response = self._llm_gateway.generate(request)
            llm_elapsed = time.time() - llm_start
            logger.info("[ProfileFusion] Step4-LLM: ✅ LLM 响应完成: latency=%dms, 实际耗时=%.3fs",
                       response.latency_ms, llm_elapsed)

            # 调试：打印响应内容
            if response.raw_text:
                raw_text_size = len(response.raw_text)
                preview = response.raw_text[:500] if raw_text_size > 500 else response.raw_text
                logger.info("[ProfileFusion] Step4-LLM: raw_text 大小: %d chars (%.1f KB)",
                           raw_text_size, raw_text_size / 1024)
                logger.info("[ProfileFusion] Step4-LLM: raw_text 预览 (前500字符):\n%s...", preview)
            if response.structured_data:
                logger.info("[ProfileFusion] Step4-LLM: structured_data 存在, keys=%s",
                           list(response.structured_data.keys()) if isinstance(response.structured_data, dict) else type(response.structured_data))

            if response.structured_data:
                logger.info("[ProfileFusion] Step4-LLM: 使用 structured_data 解析")
                result = self._parse_fusion_response(response.structured_data, profiles)
                logger.info("[ProfileFusion] Step4-LLM: 解析成功, fused_profile_id=%s, fusion_strategy=llm_fusion",
                           result.fused_profile_id)
                return result
            elif response.raw_text:
                # 尝试从 raw_text 解析 JSON
                logger.info("[ProfileFusion] Step4-LLM: 尝试从 raw_text 解析 JSON")
                result = self._parse_raw_response(response.raw_text, profiles)
                logger.info("[ProfileFusion] Step4-LLM: 解析成功, fused_profile_id=%s, fusion_strategy=llm_fusion",
                           result.fused_profile_id)
                return result
            else:
                logger.warning("[ProfileFusion] Step4-LLM: LLM 返回空内容，使用 fallback")
                result = self._fallback_fusion(profiles, collected)
                result.fusion_strategy = "fallback_empty_response"
                return result
        except Exception as e:
            llm_elapsed = time.time() - llm_start
            logger.error("[ProfileFusion] Step4-LLM: ❌ LLM 融合失败: %s, 耗时=%.3fs", str(e), llm_elapsed, exc_info=True)
            result = self._fallback_fusion(profiles, collected)
            result.fusion_strategy = "fallback_exception"
            return result

    def _build_experts_info(
        self,
        profiles: list[WorkerProfileContent],
        collected: dict,
    ) -> str:
        """构建专家信息字符串（用于 Prompt）"""
        parts = []

        for i, profile in enumerate(profiles, 1):
            expert_parts = []
            name = profile.display_name or profile.worker_id
            expert_parts.append(f"### 专家 {i}: {name}")

            # soul.md
            if profile.soul_md:
                soul_preview = profile.soul_md[:800]
                expert_parts.append(f"**核心身份(Soul)**:\n{soul_preview}")

            # identity.md
            identity = profile.contents.get("identity.md")
            if identity:
                identity_preview = identity[:500]
                expert_parts.append(f"**身份(Identity)**:\n{identity_preview}")

            # memory.md
            memory = profile.contents.get("memory.md")
            if memory:
                memory_preview = memory[:500]
                expert_parts.append(f"**经验(Memory)**:\n{memory_preview}")

            # 技能
            if profile.skill_sets:
                skill_names = [s.name for s in profile.skill_sets[:10]]
                expert_parts.append(f"**技能**: {', '.join(skill_names)}")

            parts.append("\n".join(expert_parts))

        return "\n\n---\n\n".join(parts)

    def _parse_fusion_response(
        self,
        data: dict,
        profiles: list[WorkerProfileContent],
    ) -> FusedProfile:
        """解析 LLM 返回的结构化 JSON"""
        from src.domain.models.profile_fusion import ExpertProfile

        participant_ids = [p.worker_id for p in profiles]

        # 解析 skills - 直接使用字符串列表
        skills_data = data.get("skills", [])
        skills = []
        for s in skills_data:
            if isinstance(s, str):
                skills.append(s)
            elif isinstance(s, dict) and "name" in s:
                skills.append(s["name"])

        # 构建专家 profile 列表（保存完整信息，用于后续引用）
        expert_profiles = []
        for profile in profiles:
            expert = ExpertProfile(
                worker_id=profile.worker_id,
                display_name=profile.display_name,
                description=profile.description,
                role=profile.display_name,
                # 核心 Markdown 内容
                soul=profile.soul_md,
                agents=profile.agents_md,
                tools=profile.tools_md,
                # 扩展内容（包含所有 .md 文件）
                contents=dict(profile.contents) if profile.contents else {},
                # 常用字段便于直接访问
                identity=profile.contents.get("identity.md") if profile.contents else None,
                memory=profile.contents.get("memory.md") if profile.contents else None,
                # 技能
                skills=[s.name for s in profile.skill_sets],
                # 元数据
                metadata=dict(profile.metadata) if profile.metadata else {},
            )
            expert_profiles.append(expert)

        # 解析 persona（新格式）或兼容旧格式
        persona = data.get("persona")
        if not persona:
            # 兼容旧格式：合并 soul + identity
            soul = data.get("soul", "")
            identity = data.get("identity", "")
            if soul and identity:
                persona = f"{soul}\n\n---\n\n{identity}"
            elif soul:
                persona = soul
            elif identity:
                persona = identity

        return FusedProfile(
            fused_profile_id=f"fused-{uuid.uuid4().hex[:8]}",
            source_participants=participant_ids,
            name=data.get("name"),
            description=data.get("description"),
            persona=persona,
            memory=data.get("memory"),
            skills=skills,
            expert_profiles=expert_profiles,
            participant_count=len(profiles),
            created_at=datetime.now().isoformat(),
        )

    def _parse_raw_response(
        self,
        raw_text: str,
        profiles: list[WorkerProfileContent],
    ) -> FusedProfile:
        """尝试从 raw_text 解析 JSON"""
        # 尝试提取 JSON 块
        json_match = re.search(r'```json\s*(.*?)\s*```', raw_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                logger.info("[ProfileFusion] 成功从 ```json``` 块解析 JSON")
                return self._parse_fusion_response(data, profiles)
            except json.JSONDecodeError as e:
                logger.warning("[ProfileFusion] JSON 块解析失败: %s", str(e))

        # 尝试直接解析
        try:
            data = json.loads(raw_text)
            logger.info("[ProfileFusion] 成功直接解析 JSON")
            return self._parse_fusion_response(data, profiles)
        except json.JSONDecodeError as e:
            logger.warning("[ProfileFusion] 直接解析 JSON 失败: %s，使用降级策略", str(e))
            # 重新构建 collected，确保 fallback 有内容
            collected, _ = self._collect_and_dedup(profiles)
            return self._fallback_fusion(profiles, collected)

    def _fallback_fusion(
        self,
        profiles: list[WorkerProfileContent],
        collected: dict,
    ) -> FusedProfile:
        """降级融合策略：简单拼接"""
        from src.domain.models.profile_fusion import ExpertProfile

        participant_ids = [p.worker_id for p in profiles]

        # 生成名称
        names = [p.display_name or p.worker_id for p in profiles]
        unique_names = list(dict.fromkeys(names))
        if len(unique_names) == 1:
            name = f"超级BOT：{unique_names[0]}"
        elif len(unique_names) <= 3:
            name = f"专家团队：{'+'.join(unique_names)}"
        else:
            name = f"专家团队：{'+'.join(unique_names[:3])}等{len(unique_names)}位专家"

        # 简单拼接各内容
        soul_parts = [c[1] for c in collected.get("soul", [])]
        identity_parts = [c[1] for c in collected.get("identity", [])]
        memory_parts = [c[1] for c in collected.get("memory", [])]

        # 合并 soul + identity 为 persona
        persona_parts = []
        if soul_parts:
            persona_parts.append("## 核心身份定位\n" + "\n\n---\n\n".join(soul_parts))
        if identity_parts:
            persona_parts.append("## 身份与背景\n" + "\n\n---\n\n".join(identity_parts))
        persona = "\n\n".join(persona_parts) if persona_parts else None

        # 构建专家 profile 列表（用于后续引用）
        expert_profiles = []
        for profile in profiles:
            expert = ExpertProfile(
                worker_id=profile.worker_id,
                display_name=profile.display_name,
                role=profile.display_name,
                soul=profile.soul_md,
                identity=profile.contents.get("identity.md"),
                memory=profile.contents.get("memory.md"),
                skills=[s.name for s in profile.skill_sets],
            )
            expert_profiles.append(expert)

        return FusedProfile(
            fused_profile_id=f"fused-{uuid.uuid4().hex[:8]}",
            source_participants=participant_ids,
            name=name,
            description=f"由 {len(profiles)} 位专家能力融合而成的超级 BOT",
            persona=persona,
            memory="\n\n---\n\n".join(memory_parts) if memory_parts else None,
            skills=self._merge_skills(profiles),
            expert_profiles=expert_profiles,
            participant_count=len(profiles),
            created_at=datetime.now().isoformat(),
        )

    def _merge_skills(self, profiles: list[WorkerProfileContent]) -> list[str]:
        """合并技能集（按名称去重）"""
        skill_names = set()
        for profile in profiles:
            for skill in profile.skill_sets:
                skill_names.add(skill.name)
        return sorted(list(skill_names))

    def _restore_fused_profile(
        self,
        fuse_detail: dict,
        profiles: list[WorkerProfileContent],
    ) -> "FusedProfile":
        """
        从存储的 fuse_detail 恢复 FusedProfile 对象

        Args:
            fuse_detail: 存储的融合详情字典
            profiles: WorkerProfileContent 列表（用于构建 expert_profiles）

        Returns:
            FusedProfile 对象
        """
        participant_ids = [p.worker_id for p in profiles]

        # 解析 skills - 直接使用字符串列表
        skills_data = fuse_detail.get("skills", [])
        skills = []
        for s in skills_data:
            if isinstance(s, str):
                skills.append(s)
            elif isinstance(s, dict) and "name" in s:
                skills.append(s["name"])

        # 构建 expert_profiles
        expert_profiles = []
        for profile in profiles:
            expert = ExpertProfile(
                worker_id=profile.worker_id,
                display_name=profile.display_name,
                role=profile.display_name,
                soul=profile.soul_md,
                identity=profile.contents.get("identity.md") if profile.contents else None,
                memory=profile.contents.get("memory.md") if profile.contents else None,
                skills=[s.name for s in profile.skill_sets] if profile.skill_sets else [],
            )
            expert_profiles.append(expert)

        # 解析 persona（新格式）或兼容旧格式
        persona = fuse_detail.get("persona")
        if not persona:
            # 兼容旧格式：合并 soul + identity
            soul = fuse_detail.get("soul", "")
            identity = fuse_detail.get("identity", "")
            if soul and identity:
                persona = f"{soul}\n\n---\n\n{identity}"
            elif soul:
                persona = soul
            elif identity:
                persona = identity

        # 从缓存恢复时标记 from_cache
        return FusedProfile(
            fused_profile_id=fuse_detail.get("fused_profile_id", f"fused-{uuid.uuid4().hex[:8]}"),
            source_participants=participant_ids,
            name=fuse_detail.get("name"),
            description=fuse_detail.get("description"),
            persona=persona,
            memory=fuse_detail.get("memory"),
            skills=skills,
            expert_profiles=expert_profiles,
            participant_count=len(profiles),
            created_at=datetime.now().isoformat(),
            from_cache=True,
        )


__all__ = [
    "ProfileMergeService",
]