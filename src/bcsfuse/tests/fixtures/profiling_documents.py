"""
Profiling Documents Fixtures

M2: Worker Profiling & Extraction

提供典型的 profiling 输入文档样例，供单元测试和集成测试复用。

原则：
- 样例足够典型
- 样例足够小
- 可复用
"""

from __future__ import annotations

from src.domain.models.profiling_input import (
    DocType,
    MarkdownDocument,
    SkillMetadataInput,
    ResourceMetadataInput,
    ProfilingInput,
    SkillSource,
    TrustLevel,
    ResourceKind,
    ResourceAccess,
)


# =============================================================================
# SOUL.md 样例
# =============================================================================

SOUL_COMPLETE = """
# Identity
Name: Research Bot
Role: Research Assistant

# Capabilities
- Information Retrieval (expert)
- Data Analysis (advanced)
- Report Generation (intermediate)

# Domains
- architecture
- documentation
- research

# Responsibilities
- Research and information gathering
- Data analysis and reporting
- Team coordination

# Collaboration Style
Preference: async_communication
Details: Prefer asynchronous communication with 24-hour response time.

# Skills
- web_search (builtin, trusted)
- data_processing (managed, guarded)
- email_sender (plugin, sandbox_only, approval_required)

# Resources
- Internal Wiki (api, read)
- Project Repository (repo, write)
"""

SOUL_MINIMAL = """
# Capabilities
- Research (expert)
"""

SOUL_NOISY = """
# Random Title

```python
def some_code():
    pass
```

# Capabilities
- Real Capability (expert)

[Link](http://example.com)

**Bold text** and *italic text*
"""

SOUL_EMPTY = """
# Title

No structured content here.
"""


# =============================================================================
# RULES.md 样例
# =============================================================================

RULES_COMPLETE = """
# Constraints
- 禁止发送外部邮件
- 禁止访问生产数据库
- 写生产数据库前必须审批
- 可读取内部文档

# Escalation Triggers
- 遇到高安全风险操作时上报给 human reviewer
- 超出权限范围时上报给 team lead
"""

RULES_MINIMAL = """
# Constraints
- 禁止外部访问
"""

RULES_EMPTY = """
# Title

No constraints here.
"""


# =============================================================================
# MEMORY.md 样例
# =============================================================================

MEMORY_COMPLETE = """
# 2026-03-01
该 bot 在多 agent 组队任务中表现稳定，协作能力强。

# 2026-03-15
成功完成调研任务，输出了高质量报告，获得了团队好评。

# 2026-03-20
参与架构评审会议，提出了有价值的建议。
"""

MEMORY_SINGLE = """
# 2026-03-01
Single memory entry.
"""

MEMORY_EMPTY = """
# Title

No dated entries here.
"""


# =============================================================================
# Skills 元数据样例
# =============================================================================

def get_web_search_skill() -> SkillMetadataInput:
    """获取 web_search 技能样例"""
    return SkillMetadataInput(
        name="web_search",
        source=SkillSource.BUILTIN,
        trust_level=TrustLevel.TRUSTED,
        approval_required=False,
    )


def get_email_sender_skill() -> SkillMetadataInput:
    """获取 email_sender 技能样例"""
    return SkillMetadataInput(
        name="email_sender",
        source=SkillSource.PLUGIN,
        trust_level=TrustLevel.SANDBOX_ONLY,
        approval_required=True,
    )


def get_duplicate_skill() -> SkillMetadataInput:
    """获取与 SOUL 中重复的技能样例"""
    return SkillMetadataInput(
        name="web_search",
        source=SkillSource.BUILTIN,
        trust_level=TrustLevel.TRUSTED,
    )


# =============================================================================
# Resources 元数据样例
# =============================================================================

def get_wiki_resource() -> ResourceMetadataInput:
    """获取 Wiki 资源样例"""
    return ResourceMetadataInput(
        id="res_wiki",
        name="Internal Wiki",
        kind=ResourceKind.API,
        access=ResourceAccess.READ,
    )


def get_repo_resource() -> ResourceMetadataInput:
    """获取 Repository 资源样例"""
    return ResourceMetadataInput(
        id="res_repo",
        name="Project Repository",
        kind=ResourceKind.REPO,
        access=ResourceAccess.WRITE,
    )


def get_duplicate_resource() -> ResourceMetadataInput:
    """获取与已有资源重复的资源样例"""
    return ResourceMetadataInput(
        id="res_wiki",
        name="Wiki Duplicate",
        kind=ResourceKind.FILE,
        access=ResourceAccess.READ,
    )


# =============================================================================
# 组合输入样例
# =============================================================================

def get_complete_profiling_input() -> ProfilingInput:
    """获取完整的 profiling 输入样例"""
    soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=SOUL_COMPLETE)
    rules_doc = MarkdownDocument(doc_type=DocType.RULES, content=RULES_COMPLETE)
    memory_doc = MarkdownDocument(doc_type=DocType.MEMORY, content=MEMORY_COMPLETE)

    return ProfilingInput(
        worker_id="wrk_complete_001",
        documents=[soul_doc, rules_doc, memory_doc],
        skills=[get_web_search_skill(), get_email_sender_skill()],
        resources=[get_wiki_resource(), get_repo_resource()],
    )


def get_partial_profiling_input() -> ProfilingInput:
    """获取部分成功的 profiling 输入样例（有 SOUL 和空 RULES）"""
    soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=SOUL_MINIMAL)
    rules_doc = MarkdownDocument(doc_type=DocType.RULES, content=RULES_EMPTY)

    return ProfilingInput(
        worker_id="wrk_partial_001",
        documents=[soul_doc, rules_doc],
    )


def get_duplicate_input() -> ProfilingInput:
    """获取有重复项的 profiling 输入样例"""
    soul_doc = MarkdownDocument(
        doc_type=DocType.SOUL,
        content="# Skills\n- web_search (builtin, trusted)\n# Resources\n- Internal Wiki (api, read)",
    )

    return ProfilingInput(
        worker_id="wrk_duplicate_001",
        documents=[soul_doc],
        skills=[get_duplicate_skill()],
        resources=[get_duplicate_resource()],
    )


__all__ = [
    # SOUL samples
    "SOUL_COMPLETE",
    "SOUL_MINIMAL",
    "SOUL_NOISY",
    "SOUL_EMPTY",
    # RULES samples
    "RULES_COMPLETE",
    "RULES_MINIMAL",
    "RULES_EMPTY",
    # MEMORY samples
    "MEMORY_COMPLETE",
    "MEMORY_SINGLE",
    "MEMORY_EMPTY",
    # Skill factories
    "get_web_search_skill",
    "get_email_sender_skill",
    "get_duplicate_skill",
    # Resource factories
    "get_wiki_resource",
    "get_repo_resource",
    "get_duplicate_resource",
    # Combined inputs
    "get_complete_profiling_input",
    "get_partial_profiling_input",
    "get_duplicate_input",
]