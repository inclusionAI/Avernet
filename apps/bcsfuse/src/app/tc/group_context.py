"""Preserve optional group question rewriting without the retired recommend route."""

import logging

logger = logging.getLogger(__name__)


async def rewrite_with_group_context(question, group_id, trace_id=""):
    from app.adapters.http.dependencies.fusion_dependencies import (
        get_question_rewrite_service,
    )

    service = get_question_rewrite_service()
    if service is None:
        return question
    try:
        result = await service.rewrite(question=question, group_id=group_id)
        return result.rewritten_question if result.context_messages_count else question
    except Exception as error:  # noqa: BLE001 -- optional group context degrades to the original question
        logger.warning(
            "TC group rewrite unavailable: trace_id=%s error_type=%s",
            trace_id,
            type(error).__name__,
        )
        return question
