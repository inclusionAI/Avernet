"""Row encoding shared by repository projections over Topic analysis JSON."""

from __future__ import annotations

from agentclaw.community.core.task.forum.models import ForumTopicAnalysisRecord


def analysis_to_row(analysis: ForumTopicAnalysisRecord) -> dict[str, object]:
    """Encode the known v1 sections of the topic-derived projection."""

    result: dict[str, object] = {}
    if analysis.summary is not None:
        result["summary"] = {"content": analysis.summary.content}
    result["tags"] = list(analysis.tags)
    if analysis.poll_result is not None:
        result["poll_stats"] = {
            "total_votes": analysis.poll_result.total_votes,
            "participant_count": analysis.poll_result.participant_count,
            "by_option": [
                {
                    "index": option.index,
                    "votes": option.votes,
                    "percent": option.percent,
                }
                for option in analysis.poll_result.options
            ],
        }
    result["poll_cursor_id"] = analysis.poll_cursor_id
    return result


__all__ = ["analysis_to_row"]
