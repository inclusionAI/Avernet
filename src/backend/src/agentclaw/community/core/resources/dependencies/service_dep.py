"""Yuque permission sync — fire-and-forget helper used by routes."""
from __future__ import annotations

from typing import TYPE_CHECKING

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import SubResourceItem

if TYPE_CHECKING:
    from agentclaw.community.core.resources.repository.protocol import (
        ResourceRepositoryProtocol,
    )
    from agentclaw.community.plugin_api.passport import PassportPlugin

logger = get_logger()


_YUQUE_MCP_CODE = "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver"


def sync_yuque_permissions(
    bot_id: str,
    user_id: str,
    repository: "ResourceRepositoryProtocol",
    passport: "PassportPlugin",
) -> None:
    """Read all bot's yuque links, build the neutral ``SubResourceItem`` list,
    and call the injected ``PassportPlugin.save_sub_resources``.

    This is a full (not incremental) update. Failure is logged but does not
    interrupt the caller — permissions are eventual-consistency. In the community
    build the injected passport plugin is a no-op.
    """
    try:
        from agentclaw.community.core.resources.models import ResourceType

        all_links = repository.list_resources(
            resource_type=ResourceType.LINK.value,
            parent_path=None,
            bolt_id=bot_id,
            user_id=user_id,
            status="active",
        )
        yuque_links = [
            item for item in all_links
            if (item.get("attributes") or {}).get("link_type") == "yuque"
        ]

        sub_resources = []
        for item in yuque_links:
            attrs = item.get("attributes") or {}
            url = attrs.get("url", "")
            doc_id = attrs.get("doc_id")
            book_id = attrs.get("book_id")
            yuque_type = attrs.get("yuque_type", "Doc")

            sub_resource_type = "YUQUE_BOOK" if yuque_type == "Book" else "YUQUE_DOC"

            access_modes = attrs.get("access_modes") or ["READ"]
            detail_config: dict = {
                "access_modes": access_modes,
                "server_code": _YUQUE_MCP_CODE,
            }
            if doc_id:
                detail_config["doc_id"] = str(doc_id)
            if book_id:
                detail_config["book_id"] = str(book_id)

            sub_resources.append(SubResourceItem(
                resource_type="MCP_TOOL",
                sub_resource_type=sub_resource_type,
                sub_resource_code=url,
                detail_config=detail_config,
            ))

        logger.info(
            "[sync_yuque_permissions] Sending to passport: bot_id=%s, user_id=%s, count=%d, sub_resources=%s",
            bot_id, user_id, len(sub_resources),
            [{"code": s.sub_resource_code, "type": s.sub_resource_type, "detail": s.detail_config} for s in sub_resources],
        )
        result = passport.save_sub_resources(bot_id, user_id, sub_resources)
        logger.info(
            "[sync_yuque_permissions] Passport result: bot_id=%s, success=%s",
            bot_id, result,
        )
    except Exception as exc:
        logger.warning(
            "[sync_yuque_permissions] failed: bot_id=%s, error=%s",
            bot_id, exc, exc_info=True,
        )
