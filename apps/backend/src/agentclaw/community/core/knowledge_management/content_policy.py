"""Content command validation, independent of HTTP and provider sessions."""

from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

MAX_UPLOAD = 20 * 1024 * 1024


def validate_upload_name(filename: str) -> str:
    if (
        "/" in filename
        or "\\" in filename
        or not filename.lower().endswith((".txt", ".md", ".markdown"))
    ):
        raise KnowledgeManagementError("KNOWLEDGE_UPLOAD_TYPE_UNSUPPORTED")
    return "text/plain" if filename.lower().endswith(".txt") else "text/markdown"


def validate_sync_run(payload, *, kb_id: str, source_id: str, run_id: str) -> None:
    target = payload.get("target") if isinstance(payload, dict) else None
    if not isinstance(target, dict) or payload.get("run_id") != run_id:
        raise KnowledgeManagementError("KNOWLEDGE_SYNC_RUN_INVALID")
    if target.get("kb_id") != kb_id or target.get("source_id") != source_id:
        raise KnowledgeManagementError("KNOWLEDGE_SYNC_RUN_NOT_FOUND")
