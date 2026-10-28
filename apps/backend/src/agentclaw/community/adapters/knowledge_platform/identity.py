"""Default identity-preserving resolver; deployments can validate a directory."""

from agentclaw.community.kernel.knowledge import require_knowledge_id


class DirectKnowledgeIdentity:
    def staff_no(self, user_id: str) -> str:
        return require_knowledge_id(user_id, "user_id")
