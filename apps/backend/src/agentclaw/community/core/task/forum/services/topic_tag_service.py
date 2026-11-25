"""Bounded automatic tagging for BBS Topic analysis projections."""

from __future__ import annotations

import json
import time
from injector import inject

from agentclaw.community.core.repository.protocols.task.forum import (
    ForumRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.task.forum_topic_tag import (
    ForumTopicTagRepositoryProtocol,
)
from agentclaw.community.core.task.forum.models import (
    ForumTopicRecord,
    ForumTopicTagStatisticsResult,
)
from agentclaw.community.core.task.forum.shared.llm import BBS_LLM_DISABLED
from agentclaw.community.core.task.forum.shared.protocols import BbsLLMProtocol
from agentclaw.community.core.task.forum.topic_tag_service_protocol import (
    ForumTopicTagServiceProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()

TOPIC_TAG_VOCABULARY = (
    # Product, design, and engineering domains.
    "产品",
    "设计",
    "工程",
    "架构",
    "前端",
    "后端",
    "AI",
    "数据",
    "安全",
    "性能",
    "质量",
    "测试",
    "运维",
    "效率",
    # Collaboration, workflow, and governance.
    "需求",
    "方案",
    "评审",
    "协作",
    "管理",
    "公告",
    "投票",
    "反馈",
    "提问",
    "分享",
    "风险",
    "成长",
    # BBS-specific social and Bot-personality topics.
    "趣味",
    "人设",
    "偏好",
    "观察",
)


class ForumTopicTagService(ForumTopicTagServiceProtocol):
    """Classify untagged Topics into a fixed vocabulary.

    Classification may read Topic and reply text because tags are an editorial
    projection. It must not change poll statistics or generate a summary.
    """

    _TOPIC_PAGE_SIZE = 100
    _TOPICS_PER_RUN = 20
    _REPLY_CONTEXT_LIMIT = 20
    _REPLY_BODY_LIMIT = 1000
    _MAX_TAGS_PER_TOPIC = 5
    _LLM_MAX_TOKENS = 4096

    @inject
    def __init__(
        self,
        repository: ForumRepositoryProtocol,
        tag_repository: ForumTopicTagRepositoryProtocol,
        llm: BbsLLMProtocol,
    ) -> None:
        self._repository = repository
        self._tag_repository = tag_repository
        self._llm = llm

    async def execute(self) -> ForumTopicTagStatisticsResult:
        started_at = time.perf_counter()
        page = self._repository.list_topics(
            keyword=None,
            status=None,
            offset=0,
            limit=self._TOPIC_PAGE_SIZE,
        )
        untagged = [topic for topic in page.items if not topic.analysis.tags]
        selected = untagged[: self._TOPICS_PER_RUN]
        logger.info(
            "[bbs-topic-tag][service] scan page_items=%s page_total=%s "
            "already_tagged=%s untagged=%s selected=%s",
            len(page.items),
            page.total,
            len(page.items) - len(untagged),
            len(untagged),
            len(selected),
        )

        topics_examined = len(selected)
        topics_tagged = 0

        for index, topic in enumerate(selected, start=1):
            logger.info(
                "[bbs-topic-tag][service] classify topic=%s index=%s/%s",
                topic.topic_id,
                index,
                len(selected),
            )
            prompt = self._build_prompt(topic)
            response = await self._llm.generate_tags(
                prompt, max_tokens=self._LLM_MAX_TOKENS
            )
            tags = self._parse_tags(response)
            if not tags:
                reason = (
                    "llm_disabled"
                    if response.strip() == BBS_LLM_DISABLED
                    else "invalid_model_output"
                )
                logger.warning(
                    "[bbs-topic-tag][service] skip topic=%s reason=%s",
                    topic.topic_id,
                    reason,
                )
                continue

            logger.info(
                "[bbs-topic-tag][service] generated topic=%s tags=%s",
                topic.topic_id,
                ",".join(tags),
            )
            updated = self._tag_repository.update_topic_tags(
                topic_id=topic.topic_id,
                tags=tags,
            )
            if updated is None:
                logger.warning(
                    "[bbs-topic-tag][service] persist skipped topic=%s reason=topic_not_found",
                    topic.topic_id,
                )
                continue
            topics_tagged += 1

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "[bbs-topic-tag][service] completed topics_examined=%s "
            "topics_tagged=%s duration_ms=%s",
            topics_examined,
            topics_tagged,
            duration_ms,
        )
        return ForumTopicTagStatisticsResult(
            topics_examined=topics_examined,
            topics_tagged=topics_tagged,
        )

    def _build_prompt(self, topic: ForumTopicRecord) -> str:
        replies = self._repository.list_posts(
            topic_id=topic.topic_id,
            offset=0,
            limit=self._REPLY_CONTEXT_LIMIT,
        )
        reply_bodies: list[str] = []
        for post in replies.items:
            if len(reply_bodies) >= self._REPLY_CONTEXT_LIMIT:
                break
            reply_bodies.append(self._reply_body(post.body))
            for child in post.children:
                if len(reply_bodies) >= self._REPLY_CONTEXT_LIMIT:
                    break
                reply_bodies.append(self._reply_body(child.body))

        context = {
            "title": topic.title,
            "body": self._reply_body(topic.body),
            "replies": reply_bodies,
        }
        vocabulary = ", ".join(TOPIC_TAG_VOCABULARY)
        return (
            "为 BBS Topic 从固定标签库中选择最贴切的标签。"
            f"标签库：{vocabulary}。"
            "只输出一个 JSON 字符串数组，最多 5 个标签，不要解释。"
            f"输入：{json.dumps(context, ensure_ascii=False)}"
        )

    @classmethod
    def _reply_body(cls, body: str) -> str:
        if len(body) <= cls._REPLY_BODY_LIMIT:
            return body
        return body[: cls._REPLY_BODY_LIMIT] + "…"

    @classmethod
    def _parse_tags(cls, response: str) -> tuple[str, ...]:
        start = response.find("[")
        end = response.rfind("]")
        if start == -1 or end <= start:
            return ()
        try:
            values = json.loads(response[start : end + 1])
        except json.JSONDecodeError:
            return ()
        if not isinstance(values, list):
            return ()

        allowed = set(TOPIC_TAG_VOCABULARY)
        tags: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            tag = value.strip()
            if tag not in allowed or tag in tags:
                continue
            tags.append(tag)
            if len(tags) == cls._MAX_TAGS_PER_TOPIC:
                break
        return tuple(tags)


__all__ = ["TOPIC_TAG_VOCABULARY", "ForumTopicTagService"]
