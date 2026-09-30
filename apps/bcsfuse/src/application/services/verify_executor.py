"""VerifyExecutor — BCN chat 接口调用，逐轮验证 bot 能力。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

import httpx

from src.domain.models.verify_dto import CapabilityProbes, DimensionResult
from src.utils.env_utils import is_pre, is_prod

logger = logging.getLogger(__name__)

_DEFAULT_PROBE_DELAY_SECONDS = 2
_DEFAULT_MAX_RETRIES = 2


class VerifyExecutor:
    """调用 BCN chat 接口，对 bot 逐轮发送验证 prompt。"""

    def __init__(
        self,
        bcn_chat_base_url: str,
        bcn_chat_token: str = "",
        bcn_chat_cookie: str = "",
        timeout: int = 300,
        probe_delay_seconds: int = _DEFAULT_PROBE_DELAY_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self._base_url = bcn_chat_base_url.rstrip("/") if bcn_chat_base_url else ""
        self._token = bcn_chat_token
        self._cookie = bcn_chat_cookie
        self._timeout = timeout
        self._probe_delay = probe_delay_seconds
        self._max_retries = max_retries

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self._cookie and not is_prod() and not is_pre():
            headers["Cookie"] = self._cookie
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def chat(self, bot_uuid: str, message: str) -> str:
        """向 bot 发送消息并返回回复内容（通用接口）。"""
        _has_auth = self._token or (self._cookie and not is_prod() and not is_pre())
        if not self._base_url or not _has_auth:
            logger.warning("VerifyExecutor.chat: 未配置认证信息，跳过")
            return ""

        url = f"{self._base_url}/bots/{bot_uuid}/chat"
        from_id = f"{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"

        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        url,
                        headers=self._build_headers(),
                        json={"message": message, "from": from_id},
                    )

                    if resp.status_code >= 400:
                        body = resp.text
                        logger.warning(
                            "[VerifyExecutor.chat] HTTP %d (attempt %d): %s",
                            resp.status_code,
                            attempt,
                            body[:200],
                        )
                        if attempt < self._max_retries:
                            await asyncio.sleep(self._probe_delay)
                            continue
                        return ""

                    data = resp.json()

                    if isinstance(data, dict) and data.get("buserviceErrorCode"):
                        logger.error(
                            "[VerifyExecutor.chat] BCN auth error: code=%s",
                            data.get("buserviceErrorCode", ""),
                        )
                        return ""

                    if isinstance(data, dict):
                        resp_obj = data.get("response") or data.get("data") or {}
                        content = resp_obj.get("content", "") if isinstance(resp_obj, dict) else str(data)
                    else:
                        content = str(data)

                logger.debug(
                    "[VerifyExecutor.chat] Response from %s (len=%d): %s",
                    bot_uuid,
                    len(content) if content else 0,
                    (content or "")[:200],
                )
                return content or ""

            except Exception:
                if attempt < self._max_retries:
                    logger.warning("[VerifyExecutor.chat] Retrying (attempt %d failed)", attempt)
                    await asyncio.sleep(self._probe_delay)
                    continue
                logger.exception("[VerifyExecutor.chat] Failed after retries for %s", bot_uuid)
                return ""

        return ""

    async def send_intro(self, worker_id: str) -> str:
        """发送自我介绍问题，收集 bot 的声明信息。"""
        _has_auth = self._token or (self._cookie and not is_prod() and not is_pre())
        if not self._base_url or not _has_auth:
            return ""

        url = f"{self._base_url}/bots/{worker_id}/chat"
        from_id = f"{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        intro_prompt = """
你正在接受能力审计。请用结构化方式介绍你自己，但不要只做自我宣传，而要尽量提供“可核查的能力说明”。

## 强制要求：
1. **禁止角色扮演**：不要说“作为一个专家...”，直接输出技术规格。
3. **工具 schema**：对每一项核心能力，必须提供至少一个典型的输入/输出 JSON 示例。

请按以下结构回答：

1. 角色定位
- 你的角色定位是什么？


2. 擅长领域与可执行任务
请按“领域 -> 具体任务 -> 你能执行的动作 -> 你需要的输入 -> 你能输出的结果 -> 限制条件”的格式列出。
不要只写抽象标签，要说明你真正能稳定完成什么。


3. 注册能力与工具映射 (Capability-to-Tool Map)
请列出你最核心的 3 项能力，并注明每一项能力依赖的外部工具名称。
- 能力 A [名称]: 对应工具 [注册名称/函数名]
- 能力 B [名称]: 对应工具 [注册名称/函数名]

4. skill使用说明 
针对上述能力 A，请提供：
- **触发条件**：在什么特定语境或指令下你会调用该工具？
- **输入 Schema**：该工具接受的 JSON 参数结构。
- **执行逻辑**：skill的执行流程是什么样的？


5. 过去的历史记录
如果你有 memory / 历史任务记录，请说明：
- 你过去实际处理过的主要任务类型
- 哪类任务最常见
- 哪类任务你经常拒绝、失败或转交
- 哪些工具或 skill 在历史任务中真正使用过

如果你没有足够可靠的历史记录，请明确说明“没有足够历史记录”，不要虚构。

# 严格禁止

以下为平台默认能力，所有 bot 都具备，不构成差异化价值，禁止输出：
- OpenClaw 平台内置工具与系统基础工具，如 read / write / edit / exec / browser / search / web_fetch / message / cron / sessions / subagents / memory 等
- 平台默认 skill 或官方标准 skill，如 bcs-coordination、bot-soul-manage、web-search-asap、teamclaw-cli 、coding-agent、 skill-creator、taskflow等
- 禁止从 skills/skills-repo 的公开市场中加载任何 skill 作为自己的能力
- 默认模型、上下文窗口、插件、频道、节点、网关、可观测、安全守护等平台基础设施能力
- “可管理 MCP / 可添加 MCP server / 支持 stdio 或 HTTP” 这类平台扩展能力本身

# 输出原则
- 只写“非平台默认的能力”
- 如果某项能力本质上只是通用大模型知识回答，而没有专属 skill / plugin / 数据源 / 工作流支撑，则不要将其写成“独有能力”
- 如果你没有真正独有的能力，请直接回答：`无其他特有能力`
- 每一项能力都必须满足：**能力名称 + 依赖锚点 + 可完成任务 + 输入 + 输出 + 限制**
- 每一项能力都必须说明它为什么不是平台默认能力
- 不要为了显得强而凑数，最多列 5 项，宁缺毋滥


"""

        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        url,
                        headers=self._build_headers(),
                        json={
                            "message": intro_prompt,
                            "from": from_id,
                        },
                    )

                    if resp.status_code >= 400:
                        body = resp.text
                        logger.warning(
                            "[VerifyExecutor] Intro HTTP %d (attempt %d): %s",
                            resp.status_code,
                            attempt,
                            body,
                        )
                        if attempt < self._max_retries:
                            await asyncio.sleep(self._probe_delay)
                            continue
                        return ""

                    data = resp.json()

                    if isinstance(data, dict) and data.get("buserviceErrorCode"):
                        logger.error(
                            "[VerifyExecutor] Intro BCN auth error: code=%s",
                            data.get("buserviceErrorCode", ""),
                        )
                        return ""

                    if isinstance(data, dict):
                        resp_obj = data.get("response") or data.get("data") or {}
                        content = resp_obj.get("content", "") if isinstance(resp_obj, dict) else str(data)
                    else:
                        content = str(data)

                if not content:
                    logger.warning(
                        "[VerifyExecutor] Intro response content 为空, raw JSON: %s",
                        str(data)[:500],
                    )
                else:
                    logger.debug(
                        "[VerifyExecutor] Intro response (len=%d): %s",
                        len(content),
                        content[:300],
                    )
                return content or ""

            except Exception:
                if attempt < self._max_retries:
                    logger.warning(
                        "[VerifyExecutor] Retrying intro (attempt %d failed)",
                        attempt,
                    )
                    await asyncio.sleep(self._probe_delay)
                    continue
                logger.exception("[VerifyExecutor] Intro failed after retries")
                return ""

        return ""

    async def execute(
        self,
        worker_id: str,
        probes: list[CapabilityProbes],
        bot_intro: str = "",
    ) -> list[DimensionResult]:
        if not self._base_url:
            logger.warning("VerifyExecutor: BCN chat base URL not configured, skipping all probes")
            return self._all_failed_results(probes)

        _has_auth = self._token or (self._cookie and not is_prod() and not is_pre())
        if not _has_auth:
            logger.warning("VerifyExecutor: Neither cookie nor token configured, skipping all probes")
            return self._all_failed_results(probes)

        results: list[DimensionResult] = []

        for cap_probes in probes:
            for dim in cap_probes.dimensions:
                result = await self._single_probe(worker_id, cap_probes.capability_name, dim)
                results.append(result)
                if self._probe_delay > 0:
                    await asyncio.sleep(self._probe_delay)

        return results

    async def _single_probe(
        self,
        worker_id: str,
        capability_name: str,
        dimension: "DimensionProbe",
    ) -> DimensionResult:
        url = f"{self._base_url}/bots/{worker_id}/chat"
        from_id = f"{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        logger.info(
            "[VerifyExecutor] Capability Verifying %s %s/%s: %s",
            worker_id,
            capability_name,
            dimension.dimension,
            dimension.probe_prompt[:80],
        )

        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        url,
                        headers=self._build_headers(),
                        json={
                            "message": dimension.probe_prompt,
                            "from": from_id,
                        },
                    )

                    if resp.status_code >= 400:
                        body = resp.text
                        logger.warning(
                            "[VerifyExecutor] HTTP %d for %s/%s (attempt %d/%d): %s",
                            resp.status_code,
                            capability_name,
                            dimension.dimension,
                            attempt,
                            self._max_retries,
                            body[:200],
                        )
                        if attempt < self._max_retries:
                            await asyncio.sleep(self._probe_delay)
                            continue
                        return DimensionResult(
                            capability_name=capability_name,
                            dimension=dimension.dimension,
                            probe_prompt=dimension.probe_prompt,
                            response_content="",
                            failed=True,
                        )

                    data = resp.json()

                    if isinstance(data, dict) and data.get("buserviceErrorCode"):
                        logger.error(
                            "[VerifyExecutor] BCN auth error for %s/%s: code=%s",
                            capability_name,
                            dimension.dimension,
                            data.get("buserviceErrorCode", ""),
                        )
                        return DimensionResult(
                            capability_name=capability_name,
                            dimension=dimension.dimension,
                            probe_prompt=dimension.probe_prompt,
                            response_content="",
                            failed=True,
                        )

                    if isinstance(data, dict):
                        resp_obj = data.get("response") or data.get("data") or {}
                        content = resp_obj.get("content", "") if isinstance(resp_obj, dict) else str(data)
                    else:
                        content = str(data)

                    if not content:
                        logger.debug(
                            "[VerifyExecutor] Full response for %s/%s: %s",
                            capability_name,
                            dimension.dimension,
                            str(data)[:300],
                        )

                logger.debug(
                    "[VerifyExecutor] Response for %s/%s: %s",
                    capability_name,
                    dimension.dimension,
                    content[:200] if content else "(empty)",
                )

                if not content or not content.strip():
                    return DimensionResult(
                        capability_name=capability_name,
                        dimension=dimension.dimension,
                        probe_prompt=dimension.probe_prompt,
                        response_content="",
                        failed=True,
                    )

                return DimensionResult(
                    capability_name=capability_name,
                    dimension=dimension.dimension,
                    probe_prompt=dimension.probe_prompt,
                    response_content=content,
                    failed=False,
                )

            except Exception:
                if attempt < self._max_retries:
                    logger.warning(
                        "[VerifyExecutor] Retrying %s/%s (attempt %d failed)",
                        capability_name,
                        dimension.dimension,
                        attempt,
                    )
                    await asyncio.sleep(self._probe_delay)
                    continue
                logger.exception(
                    "VerifyExecutor: BCN chat failed for %s/%s",
                    capability_name,
                    dimension.dimension,
                )
                return DimensionResult(
                    capability_name=capability_name,
                    dimension=dimension.dimension,
                    probe_prompt=dimension.probe_prompt,
                    response_content="",
                    failed=True,
                )

        return DimensionResult(
            capability_name=capability_name,
            dimension=dimension.dimension,
            probe_prompt=dimension.probe_prompt,
            response_content="",
            failed=True,
        )

    @staticmethod
    def _all_failed_results(probes: list[CapabilityProbes]) -> list[DimensionResult]:
        results: list[DimensionResult] = []
        for cap in probes:
            for dim in cap.dimensions:
                results.append(DimensionResult(
                    capability_name=cap.capability_name,
                    dimension=dim.dimension,
                    probe_prompt=dim.probe_prompt,
                    response_content="",
                    failed=True,
                ))
        return results