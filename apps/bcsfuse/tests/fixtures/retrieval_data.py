"""
Retrieval Data Fixtures

M5: Unified Retrieval Fabric

提供典型的检索相关测试数据，供单元测试和集成测试复用。

原则：
- 样例足够典型
- 样例足够小
- 可复用
- 不被 src/ 运行时代码直接依赖
"""

from __future__ import annotations

from src.domain.models.worker import (
    Worker,
    WorkerType,
    WorkerIdentity,
    Capability,
    CapabilityLevel,
    Constraint,
    ConstraintKind,
    ConstraintSeverity,
    SkillRef,
    SkillSource,
    ResourceRef,
    ResourceKind,
    ResourceAccess,
    WorkerState,
    Availability,
    TrustLevel,
    PerformanceStats,
)
from src.domain.models.candidate_bundle import KnowledgeItem
from src.domain.models.task_spec import TaskSpec, RiskLevel, Subtask
from src.domain.models.plan_draft import PlanDraft, PlanStep


# =============================================================================
# KnowledgeItem Fixtures
# =============================================================================

def get_architecture_doc_knowledge() -> KnowledgeItem:
    """获取架构文档知识项"""
    return KnowledgeItem(
        id="kno_arch_doc_001",
        kind="doc",
        title="System Architecture Guide",
        summary="Complete guide for system architecture design patterns and best practices.",
        source_uri="docs/architecture/guide.md",
        highlights=["microservices", "event-driven", "domain-driven design"],
        freshness="fresh",
        reliability="high",
        tags=["architecture", "design", "guide"],
    )


def get_api_doc_knowledge() -> KnowledgeItem:
    """获取 API 文档知识项"""
    return KnowledgeItem(
        id="kno_api_doc_001",
        kind="doc",
        title="API Design Standards",
        summary="RESTful API design standards and conventions.",
        source_uri="docs/api/standards.md",
        highlights=["REST", "OpenAPI", "versioning"],
        freshness="fresh",
        reliability="high",
        tags=["api", "rest", "standards"],
    )


def get_security_rule_knowledge() -> KnowledgeItem:
    """获取安全规则知识项"""
    return KnowledgeItem(
        id="kno_sec_rule_001",
        kind="rule",
        title="Security Guidelines",
        summary="Security guidelines for production systems.",
        source_uri="docs/security/guidelines.md",
        highlights=["authentication", "authorization", "encryption"],
        freshness="usable",
        reliability="high",
        tags=["security", "guidelines"],
    )


def get_playbook_knowledge() -> KnowledgeItem:
    """获取运维手册知识项"""
    return KnowledgeItem(
        id="kno_playbook_001",
        kind="playbook",
        title="Incident Response Playbook",
        summary="Standard procedures for incident response.",
        source_uri="docs/playbooks/incident.md",
        highlights=["incident", "response", "escalation"],
        freshness="stale",
        reliability="medium",
        tags=["playbook", "incident"],
    )


def get_all_knowledge_items() -> list[KnowledgeItem]:
    """获取所有知识项列表"""
    return [
        get_architecture_doc_knowledge(),
        get_api_doc_knowledge(),
        get_security_rule_knowledge(),
        get_playbook_knowledge(),
    ]


# =============================================================================
# SkillRef Fixtures
# =============================================================================

def get_web_search_skill_ref() -> SkillRef:
    """获取 web_search 技能引用"""
    return SkillRef(
        name="web_search",
        source=SkillSource.BUILTIN,
        description="Search the web for information",
        trust_level=TrustLevel.TRUSTED,
        approval_required=False,
        tool_names=["web_search"],
    )


def get_code_generator_skill_ref() -> SkillRef:
    """获取代码生成技能引用"""
    return SkillRef(
        name="code_generator",
        source=SkillSource.MANAGED,
        description="Generate code based on specifications",
        trust_level=TrustLevel.GUARDED,
        approval_required=False,
        tool_names=["generate_code", "format_code"],
    )


def get_database_writer_skill_ref() -> SkillRef:
    """获取数据库写入技能引用（高风险）"""
    return SkillRef(
        name="database_writer",
        source=SkillSource.PLUGIN,
        description="Write to production database",
        trust_level=TrustLevel.SANDBOX_ONLY,
        approval_required=True,
        tool_names=["db_write", "db_update"],
    )


def get_email_sender_skill_ref() -> SkillRef:
    """获取邮件发送技能引用"""
    return SkillRef(
        name="email_sender",
        source=SkillSource.PLUGIN,
        description="Send emails to recipients",
        trust_level=TrustLevel.SANDBOX_ONLY,
        approval_required=True,
        tool_names=["send_email"],
    )


def get_all_skill_refs() -> list[SkillRef]:
    """获取所有技能引用列表"""
    return [
        get_web_search_skill_ref(),
        get_code_generator_skill_ref(),
        get_database_writer_skill_ref(),
        get_email_sender_skill_ref(),
    ]


# =============================================================================
# ResourceRef Fixtures
# =============================================================================

def get_internal_wiki_resource_ref() -> ResourceRef:
    """获取内部 Wiki 资源引用"""
    return ResourceRef(
        id="res_wiki_001",
        kind=ResourceKind.API,
        name="Internal Wiki",
        description="Internal documentation wiki",
        uri="https://wiki.internal.example.com",
        access=ResourceAccess.READ,
        owner="team-docs",
        tags=["documentation", "internal"],
    )


def get_project_repo_resource_ref() -> ResourceRef:
    """获取项目仓库资源引用"""
    return ResourceRef(
        id="res_repo_001",
        kind=ResourceKind.REPO,
        name="Project Repository",
        description="Main project source code repository",
        uri="git@github.internal.example.com:team/project.git",
        access=ResourceAccess.WRITE,
        owner="team-dev",
        tags=["source", "git"],
    )


def get_production_db_resource_ref() -> ResourceRef:
    """获取生产数据库资源引用"""
    return ResourceRef(
        id="res_prod_db_001",
        kind=ResourceKind.DATASET,
        name="Production Database",
        description="Production database cluster",
        uri="db.production.internal:5432",
        access=ResourceAccess.READ,
        owner="team-dba",
        tags=["database", "production", "sensitive"],
    )


def get_dashboard_resource_ref() -> ResourceRef:
    """获取仪表盘资源引用"""
    return ResourceRef(
        id="res_dashboard_001",
        kind=ResourceKind.DASHBOARD,
        name="Monitoring Dashboard",
        description="System monitoring and metrics dashboard",
        uri="https://grafana.internal.example.com",
        access=ResourceAccess.READ,
        owner="team-sre",
        tags=["monitoring", "metrics"],
    )


def get_all_resource_refs() -> list[ResourceRef]:
    """获取所有资源引用列表"""
    return [
        get_internal_wiki_resource_ref(),
        get_project_repo_resource_ref(),
        get_production_db_resource_ref(),
        get_dashboard_resource_ref(),
    ]


# =============================================================================
# Worker Fixtures
# =============================================================================

def get_researcher_bot() -> Worker:
    """获取研究员 bot Worker"""
    return Worker(
        id="wrk_researcher_001",
        type=WorkerType.BOT,
        identity=WorkerIdentity(
            name="Research Bot",
            handle="researcher_bot",
            title="Research Assistant",
            owner_team="team-research",
        ),
        responsibilities=["research", "information_gathering", "analysis"],
        domains=["architecture", "documentation", "research"],
        capabilities=[
            Capability(name="information_retrieval", level=CapabilityLevel.EXPERT),
            Capability(name="data_analysis", level=CapabilityLevel.ADVANCED),
            Capability(name="report_generation", level=CapabilityLevel.INTERMEDIATE),
        ],
        constraints=[],
        skills=[get_web_search_skill_ref()],
        resources=[get_internal_wiki_resource_ref()],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            current_load=0.2,
        ),
        performance_stats=PerformanceStats(
            success_rate_30d=0.95,
            task_count_30d=50,
        ),
    )


def get_architect_bot() -> Worker:
    """获取架构师 bot Worker"""
    return Worker(
        id="wrk_architect_001",
        type=WorkerType.BOT,
        identity=WorkerIdentity(
            name="Architect Bot",
            handle="architect_bot",
            title="System Architect",
            owner_team="team-architecture",
        ),
        responsibilities=["architecture_design", "technical_planning", "code_review"],
        domains=["architecture", "design", "patterns"],
        capabilities=[
            Capability(name="system_design", level=CapabilityLevel.EXPERT),
            Capability(name="code_review", level=CapabilityLevel.ADVANCED),
            Capability(name="documentation", level=CapabilityLevel.ADVANCED),
        ],
        constraints=[
            Constraint(
                kind=ConstraintKind.APPROVAL,
                rule="Architecture changes require approval from tech lead",
                severity=ConstraintSeverity.HIGH,
            ),
        ],
        skills=[get_code_generator_skill_ref()],
        resources=[get_project_repo_resource_ref(), get_internal_wiki_resource_ref()],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            current_load=0.4,
        ),
        performance_stats=PerformanceStats(
            success_rate_30d=0.92,
            task_count_30d=30,
        ),
    )


def get_developer_bot() -> Worker:
    """获取开发 bot Worker"""
    return Worker(
        id="wrk_developer_001",
        type=WorkerType.BOT,
        identity=WorkerIdentity(
            name="Developer Bot",
            handle="developer_bot",
            title="Software Developer",
            owner_team="team-dev",
        ),
        responsibilities=["coding", "testing", "debugging"],
        domains=["development", "testing", "automation"],
        capabilities=[
            Capability(name="coding", level=CapabilityLevel.ADVANCED),
            Capability(name="testing", level=CapabilityLevel.INTERMEDIATE),
            Capability(name="debugging", level=CapabilityLevel.ADVANCED),
        ],
        constraints=[],
        skills=[get_code_generator_skill_ref(), get_web_search_skill_ref()],
        resources=[get_project_repo_resource_ref()],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.GUARDED,
            current_load=0.6,
        ),
        performance_stats=PerformanceStats(
            success_rate_30d=0.88,
            task_count_30d=80,
        ),
    )


def get_human_reviewer() -> Worker:
    """获取人工审核员 Worker"""
    return Worker(
        id="wrk_reviewer_001",
        type=WorkerType.HUMAN,
        identity=WorkerIdentity(
            name="Code Reviewer",
            handle="reviewer",
            title="Senior Engineer",
            owner_team="team-core",
        ),
        responsibilities=["code_review", "approval", "mentoring"],
        domains=["architecture", "security", "best_practices"],
        capabilities=[
            Capability(name="code_review", level=CapabilityLevel.EXPERT),
            Capability(name="security_review", level=CapabilityLevel.ADVANCED),
            Capability(name="mentoring", level=CapabilityLevel.ADVANCED),
        ],
        constraints=[
            Constraint(
                kind=ConstraintKind.POLICY,
                rule="Must approve all production changes",
                severity=ConstraintSeverity.CRITICAL,
            ),
        ],
        skills=[],
        resources=[get_project_repo_resource_ref(), get_production_db_resource_ref()],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            current_load=0.3,
        ),
        performance_stats=PerformanceStats(
            success_rate_30d=0.98,
            task_count_30d=20,
        ),
    )


def get_busy_bot() -> Worker:
    """获取忙碌状态的 bot Worker"""
    return Worker(
        id="wrk_busy_001",
        type=WorkerType.BOT,
        identity=WorkerIdentity(
            name="Busy Bot",
            handle="busy_bot",
            title="Overloaded Worker",
            owner_team="team-ops",
        ),
        responsibilities=["operations", "monitoring"],
        domains=["operations"],
        capabilities=[
            Capability(name="monitoring", level=CapabilityLevel.INTERMEDIATE),
        ],
        constraints=[],
        skills=[],
        resources=[get_dashboard_resource_ref()],
        state=WorkerState(
            availability=Availability.BUSY,
            trust_level=TrustLevel.TRUSTED,
            current_load=0.95,
        ),
        performance_stats=PerformanceStats(
            success_rate_30d=0.85,
            task_count_30d=100,
        ),
    )


def get_all_workers() -> list[Worker]:
    """获取所有 Worker 列表"""
    return [
        get_researcher_bot(),
        get_architect_bot(),
        get_developer_bot(),
        get_human_reviewer(),
        get_busy_bot(),
    ]


# =============================================================================
# TaskSpec Fixtures for Retrieval
# =============================================================================

def get_architecture_design_task_spec() -> TaskSpec:
    """获取架构设计任务的 TaskSpec"""
    return TaskSpec(
        id="tsk_arch_design_001",
        goal="Design a microservices architecture for the new system",
        deliverables=["Architecture design document", "API specifications"],
        constraints=["Use event-driven pattern", "High availability required"],
        success_criteria=["Document reviewed and approved", "Technical feasibility confirmed"],
        required_capabilities=["system_design", "documentation"],
        required_knowledge=["architecture", "microservices"],
        required_resources=["res_wiki_001", "res_repo_001"],
        risk_level=RiskLevel.MEDIUM,
        unknowns=[],
        subtasks=[
            Subtask(id="st_1", title="Research patterns", objective="Research microservices patterns"),
            Subtask(id="st_2", title="Create design", objective="Create initial architecture design"),
        ],
    )


def get_research_task_spec() -> TaskSpec:
    """获取调研任务的 TaskSpec"""
    return TaskSpec(
        id="tsk_research_001",
        goal="Research best practices for API design",
        deliverables=["Research report", "Recommendations"],
        constraints=["Use internal resources only"],
        success_criteria=["Report completed", "Recommendations documented"],
        required_capabilities=["information_retrieval", "data_analysis", "report_generation"],
        required_knowledge=["api", "rest"],
        required_resources=["res_wiki_001"],
        risk_level=RiskLevel.LOW,
        unknowns=["Specific technology stack"],
        subtasks=[
            Subtask(id="st_1", title="Gather information", objective="Gather information from internal wiki"),
            Subtask(id="st_2", title="Analyze findings", objective="Analyze and summarize findings"),
        ],
    )


def get_development_task_spec() -> TaskSpec:
    """获取开发任务的 TaskSpec"""
    return TaskSpec(
        id="tsk_dev_001",
        goal="Implement user authentication module",
        deliverables=["Working authentication module", "Unit tests"],
        constraints=["Must pass security review"],
        success_criteria=["All tests pass", "Code reviewed", "Security approved"],
        required_capabilities=["coding", "testing", "debugging"],
        required_knowledge=["authentication", "security"],
        required_resources=["res_repo_001"],
        risk_level=RiskLevel.HIGH,
        unknowns=[],
        subtasks=[
            Subtask(id="st_1", title="Implement core", objective="Implement authentication core logic"),
            Subtask(id="st_2", title="Write tests", objective="Write unit tests"),
        ],
    )


# =============================================================================
# PlanDraft Fixtures for Retrieval
# =============================================================================

def get_architecture_plan_draft() -> PlanDraft:
    """获取架构设计任务的 PlanDraft"""
    return PlanDraft(
        task_id="tsk_arch_design_001",
        strategy="Research patterns first, then create design with review",
        steps=[
            PlanStep(
                id="step_1",
                title="Research patterns",
                objective="Research microservices and event-driven patterns",
                required_capabilities=["information_retrieval"],
            ),
            PlanStep(
                id="step_2",
                title="Create design",
                objective="Create architecture design document",
                required_capabilities=["system_design", "documentation"],
            ),
        ],
        role_requirements=["researcher", "architect", "reviewer"],
        knowledge_requirements=["architecture", "microservices", "event-driven"],
        resource_requirements=["res_wiki_001", "res_repo_001"],
        handoff_strategy="document_handoff",
        escalation_points=["design_conflict", "resource_constraint"],
    )


def get_research_plan_draft() -> PlanDraft:
    """获取调研任务的 PlanDraft"""
    return PlanDraft(
        task_id="tsk_research_001",
        strategy="Gather information and synthesize report",
        steps=[
            PlanStep(
                id="step_1",
                title="Gather information",
                objective="Gather information from various sources",
                required_capabilities=["information_retrieval"],
            ),
            PlanStep(
                id="step_2",
                title="Create report",
                objective="Analyze and create research report",
                required_capabilities=["data_analysis", "report_generation"],
            ),
        ],
        role_requirements=["researcher", "analyst"],
        knowledge_requirements=["api", "rest", "best_practices"],
        resource_requirements=["res_wiki_001"],
        handoff_strategy="report_handoff",
        escalation_points=["information_gap"],
    )


def get_development_plan_draft() -> PlanDraft:
    """获取开发任务的 PlanDraft"""
    return PlanDraft(
        task_id="tsk_dev_001",
        strategy="Implement iteratively with continuous testing",
        steps=[
            PlanStep(
                id="step_1",
                title="Implement",
                objective="Implement authentication module",
                required_capabilities=["coding"],
            ),
            PlanStep(
                id="step_2",
                title="Test",
                objective="Write and run tests",
                required_capabilities=["testing"],
            ),
            PlanStep(
                id="step_3",
                title="Review",
                objective="Code review and security review",
                required_capabilities=["code_review", "security_review"],
            ),
        ],
        role_requirements=["developer", "reviewer"],
        knowledge_requirements=["authentication", "security"],
        resource_requirements=["res_repo_001"],
        handoff_strategy="code_handoff",
        escalation_points=["security_concern", "performance_issue"],
    )


# =============================================================================
# Retrieval Scenario Fixtures
# =============================================================================

def get_retrieval_scenario_architecture() -> dict:
    """
    获取架构设计检索场景

    返回包含 TaskSpec、PlanDraft 和期望匹配结果的字典
    """
    return {
        "task_spec": get_architecture_design_task_spec(),
        "plan_draft": get_architecture_plan_draft(),
        "expected_worker_capabilities": ["system_design", "documentation", "information_retrieval"],
        "expected_worker_roles": ["researcher", "architect", "reviewer"],
        "expected_knowledge_tags": ["architecture", "microservices", "event-driven"],
        "expected_resource_ids": ["res_wiki_001", "res_repo_001"],
    }


def get_retrieval_scenario_research() -> dict:
    """
    获取调研任务检索场景

    返回包含 TaskSpec、PlanDraft 和期望匹配结果的字典
    """
    return {
        "task_spec": get_research_task_spec(),
        "plan_draft": get_research_plan_draft(),
        "expected_worker_capabilities": ["information_retrieval", "data_analysis", "report_generation"],
        "expected_worker_roles": ["researcher", "analyst"],
        "expected_knowledge_tags": ["api", "rest"],
        "expected_resource_ids": ["res_wiki_001"],
    }


def get_retrieval_scenario_development() -> dict:
    """
    获取开发任务检索场景

    返回包含 TaskSpec、PlanDraft 和期望匹配结果的字典
    """
    return {
        "task_spec": get_development_task_spec(),
        "plan_draft": get_development_plan_draft(),
        "expected_worker_capabilities": ["coding", "testing", "code_review"],
        "expected_worker_roles": ["developer", "reviewer"],
        "expected_knowledge_tags": ["authentication", "security"],
        "expected_resource_ids": ["res_repo_001"],
    }


__all__ = [
    # KnowledgeItem fixtures
    "get_architecture_doc_knowledge",
    "get_api_doc_knowledge",
    "get_security_rule_knowledge",
    "get_playbook_knowledge",
    "get_all_knowledge_items",
    # SkillRef fixtures
    "get_web_search_skill_ref",
    "get_code_generator_skill_ref",
    "get_database_writer_skill_ref",
    "get_email_sender_skill_ref",
    "get_all_skill_refs",
    # ResourceRef fixtures
    "get_internal_wiki_resource_ref",
    "get_project_repo_resource_ref",
    "get_production_db_resource_ref",
    "get_dashboard_resource_ref",
    "get_all_resource_refs",
    # Worker fixtures
    "get_researcher_bot",
    "get_architect_bot",
    "get_developer_bot",
    "get_human_reviewer",
    "get_busy_bot",
    "get_all_workers",
    # TaskSpec fixtures
    "get_architecture_design_task_spec",
    "get_research_task_spec",
    "get_development_task_spec",
    # PlanDraft fixtures
    "get_architecture_plan_draft",
    "get_research_plan_draft",
    "get_development_plan_draft",
    # Scenario fixtures
    "get_retrieval_scenario_architecture",
    "get_retrieval_scenario_research",
    "get_retrieval_scenario_development",
]