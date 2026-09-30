"""
Baseline Profile Extractor

M2: Worker Profiling & Extraction

最小可用的 Worker 画像抽取器。

实现原则：
- 规则优先基于 heading / bullet list / 简单关键词
- 不做复杂推理，不做复杂 NLP，不做 LLM 接入
- 支持部分成功，返回 warnings/errors
- 每个抽取结果都有真实可定位的 source reference

抽取规则（baseline）：
1. SOUL.md
   - Capabilities: 从 "Capabilities" 节抽取列表项，解析格式 "名称 (级别)"
   - Domains: 从 "Domains" 节抽取列表项
   - Responsibilities: 从 "Responsibilities" 节抽取列表项
   - Collaboration Style: 从 "Collaboration Style" 节抽取
   - Skills: 从 "Skills" 节抽取，格式 "名称 (来源, 信任级别)"
   - Resources: 从 "Resources" 节抽取，格式 "名称 (类型, 权限)"

2. RULES.md
   - Constraints: 从 "Constraints" 节抽取
     - "禁止*" -> forbidden
     - "*必须审批" / "需要审批" -> approval_required
     - "可*" -> allowed
   - Escalation Triggers: 从 "Escalation Triggers" / "Escalation" 节抽取

3. MEMORY.md
   - Episodes: 每个日期标题下的段落
"""

from __future__ import annotations

import re
from typing import Any

from src.domain.models.profiling_input import (
    ProfilingInput,
    MarkdownDocument,
    DocType,
    SkillMetadataInput,
    ResourceMetadataInput,
)
from src.domain.models.profiling_result import (
    WorkerProfileExtractionResult,
    SourceReference,
    ExtractionWarning,
    ExtractionError,
    ExtractedCapability,
    ExtractedDomain,
    ExtractedResponsibility,
    ExtractedConstraint,
    ExtractedEscalationTrigger,
    ExtractedCollaborationStyle,
    ExtractedSkill,
    ExtractedResource,
    ExtractedMemoryEpisode,
    CapabilityLevel,
    ConstraintPolicy,
    ConstraintKind,
    SkillSource,
    TrustLevel,
    ResourceKind,
    ResourceAccess,
)
from src.infra.parsers.markdown_parser import MarkdownParser


class BaselineProfileExtractor:
    """
    Baseline Worker 画像抽取器

    实现最小可用的抽取规则，支持部分成功。
    """

    # 标题别名映射（支持多种写法）
    SECTION_ALIASES: dict[str, list[str]] = {
        "capabilities": ["capabilities", "capability", "skills and capabilities", "能力"],
        "domains": ["domains", "domain", "领域"],
        "responsibilities": ["responsibilities", "responsibility", "职责"],
        "constraints": ["constraints", "constraint", "rules", "约束", "规则"],
        "escalation_triggers": ["escalation triggers", "escalation", "escalations", "上报"],
        "collaboration_style": ["collaboration style", "collaboration", "work style", "协作风格"],
        "skills": ["skills", "skill", "技能", "tools"],
        "resources": ["resources", "resource", "资源"],
    }

    # 能力级别关键词
    CAPABILITY_LEVELS: dict[str, CapabilityLevel] = {
        "expert": CapabilityLevel.EXPERT,
        "advanced": CapabilityLevel.ADVANCED,
        "intermediate": CapabilityLevel.INTERMEDIATE,
        "novice": CapabilityLevel.NOVICE,
        "专家": CapabilityLevel.EXPERT,
        "高级": CapabilityLevel.ADVANCED,
        "中级": CapabilityLevel.INTERMEDIATE,
        "初级": CapabilityLevel.NOVICE,
    }

    # 技能来源关键词
    SKILL_SOURCES: dict[str, SkillSource] = {
        "builtin": SkillSource.BUILTIN,
        "managed": SkillSource.MANAGED,
        "workspace": SkillSource.WORKSPACE,
        "plugin": SkillSource.PLUGIN,
        "mcp": SkillSource.MCP,
    }

    # 信任级别关键词
    TRUST_LEVELS: dict[str, TrustLevel] = {
        "trusted": TrustLevel.TRUSTED,
        "guarded": TrustLevel.GUARDED,
        "sandbox_only": TrustLevel.SANDBOX_ONLY,
        "sandbox": TrustLevel.SANDBOX_ONLY,
    }

    # 资源类型关键词
    RESOURCE_KINDS: dict[str, ResourceKind] = {
        "api": ResourceKind.API,
        "file": ResourceKind.FILE,
        "folder": ResourceKind.FOLDER,
        "dataset": ResourceKind.DATASET,
        "repo": ResourceKind.REPO,
        "repository": ResourceKind.REPO,
        "dashboard": ResourceKind.DASHBOARD,
    }

    # 资源访问权限关键词
    RESOURCE_ACCESS: dict[str, ResourceAccess] = {
        "read": ResourceAccess.READ,
        "write": ResourceAccess.WRITE,
        "execute": ResourceAccess.EXECUTE,
        "admin": ResourceAccess.ADMIN,
    }

    def extract(self, input_data: ProfilingInput) -> WorkerProfileExtractionResult:
        """
        从输入数据抽取 Worker 画像

        Args:
            input_data: Profiling 输入数据

        Returns:
            WorkerProfileExtractionResult: 抽取结果
        """
        result = WorkerProfileExtractionResult(worker_id=input_data.worker_id)

        # 处理每个文档
        for doc in input_data.documents:
            doc_result = self._extract_from_document(doc)
            result = result.merge(doc_result)

        # 处理技能元数据
        for skill in input_data.skills:
            extracted_skill = self._convert_skill_metadata(skill)
            if extracted_skill:
                result.skills.append(extracted_skill)

        # 处理资源元数据
        for resource in input_data.resources:
            extracted_resource = self._convert_resource_metadata(resource)
            if extracted_resource:
                result.resources.append(extracted_resource)

        # 检查并添加缺失字段的警告
        result = self._add_missing_field_warnings(result, input_data)

        return result

    def _extract_from_document(self, doc: MarkdownDocument) -> WorkerProfileExtractionResult:
        """从单个文档抽取"""
        result = WorkerProfileExtractionResult(worker_id="")

        parser = MarkdownParser(doc.content)

        if doc.doc_type == DocType.SOUL:
            result = self._extract_from_soul(doc, parser)
        elif doc.doc_type == DocType.RULES:
            result = self._extract_from_rules(doc, parser)
        elif doc.doc_type == DocType.MEMORY:
            result = self._extract_from_memory(doc, parser)

        return result

    def _normalize_section_name(self, heading_text: str) -> str | None:
        """标准化节名称"""
        heading_lower = heading_text.lower().strip()
        for canonical, aliases in self.SECTION_ALIASES.items():
            if heading_lower in aliases:
                return canonical
        return None

    def _make_source_ref(
        self,
        doc: MarkdownDocument,
        section: str | None = None,
        heading: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        snippet: str | None = None,
    ) -> SourceReference:
        """创建来源引用"""
        # 使用标准文档名称（首字母大写）
        default_doc_name = f"{doc.doc_type.value.upper()}.md"
        params: dict[str, Any] = {
            "doc_type": doc.doc_type,
            "doc_name": doc.source_uri or default_doc_name,
        }
        if section:
            params["section"] = section
        if heading:
            params["heading"] = heading
        if line_start is not None:
            params["line_start"] = line_start
        if line_end is not None:
            params["line_end"] = line_end
        if snippet:
            params["snippet"] = snippet

        return SourceReference(**params)

    # =========================================================================
    # SOUL.md 抽取
    # =========================================================================

    def _extract_from_soul(
        self, doc: MarkdownDocument, parser: MarkdownParser
    ) -> WorkerProfileExtractionResult:
        """从 SOUL.md 抽取"""
        result = WorkerProfileExtractionResult(worker_id="")

        # 抽取能力
        capabilities = self._extract_capabilities(doc, parser)
        result.capabilities.extend(capabilities)

        # 抽取领域
        domains = self._extract_domains(doc, parser)
        result.domains.extend(domains)

        # 抽取职责
        responsibilities = self._extract_responsibilities(doc, parser)
        result.responsibilities.extend(responsibilities)

        # 抽取协作风格
        collaboration_style = self._extract_collaboration_style(doc, parser)
        if collaboration_style:
            result.collaboration_style = collaboration_style

        # 抽取技能
        skills = self._extract_skills_from_soul(doc, parser)
        result.skills.extend(skills)

        # 抽取资源
        resources = self._extract_resources_from_soul(doc, parser)
        result.resources.extend(resources)

        return result

    def _extract_capabilities(
        self, doc: MarkdownDocument, parser: MarkdownParser
    ) -> list[ExtractedCapability]:
        """抽取能力"""
        capabilities: list[ExtractedCapability] = []

        # 找到 Capabilities 节
        section_content = self._find_section_content(parser, "capabilities")
        if not section_content:
            return capabilities

        section_parser = MarkdownParser(section_content)
        lists = section_parser.get_lists()

        for lst in lists:
            for item in lst["items"]:
                # 跳过嵌套项
                if item.startswith("  "):
                    continue

                cap = self._parse_capability_item(item, doc, lst.get("heading_context"))
                if cap:
                    capabilities.append(cap)

        return capabilities

    def _parse_capability_item(
        self, item: str, doc: MarkdownDocument, context: str | None
    ) -> ExtractedCapability | None:
        """解析能力项，格式: "名称 (级别)" 或 "名称" """
        # 尝试匹配 "名称 (级别)" 格式
        pattern = r"^(.+?)\s*\((.+?)\)\s*$"
        match = re.match(pattern, item.strip())

        if match:
            name = match.group(1).strip()
            level_str = match.group(2).strip().lower()

            level = self.CAPABILITY_LEVELS.get(level_str, CapabilityLevel.NOVICE)
        else:
            name = item.strip()
            level = CapabilityLevel.NOVICE

        if not name:
            return None

        return ExtractedCapability(
            name=name,
            level=level,
            confidence=0.9 if match else 0.7,  # 有级别信息置信度更高
            source_ref=self._make_source_ref(doc, section="Capabilities", snippet=item),
        )

    def _extract_domains(
        self, doc: MarkdownDocument, parser: MarkdownParser
    ) -> list[ExtractedDomain]:
        """抽取领域"""
        domains: list[ExtractedDomain] = []

        section_content = self._find_section_content(parser, "domains")
        if not section_content:
            return domains

        section_parser = MarkdownParser(section_content)
        lists = section_parser.get_lists()

        for lst in lists:
            for item in lst["items"]:
                if item.startswith("  "):
                    continue

                name = item.strip()
                if name:
                    domains.append(ExtractedDomain(
                        name=name,
                        confidence=0.9,
                        source_ref=self._make_source_ref(doc, section="Domains", snippet=item),
                    ))

        return domains

    def _extract_responsibilities(
        self, doc: MarkdownDocument, parser: MarkdownParser
    ) -> list[ExtractedResponsibility]:
        """抽取职责"""
        responsibilities: list[ExtractedResponsibility] = []

        section_content = self._find_section_content(parser, "responsibilities")
        if not section_content:
            return responsibilities

        section_parser = MarkdownParser(section_content)
        lists = section_parser.get_lists()

        for lst in lists:
            for item in lst["items"]:
                if item.startswith("  "):
                    continue

                desc = item.strip()
                if desc:
                    responsibilities.append(ExtractedResponsibility(
                        description=desc,
                        confidence=0.9,
                        source_ref=self._make_source_ref(doc, section="Responsibilities", snippet=item),
                    ))

        return responsibilities

    def _extract_collaboration_style(
        self, doc: MarkdownDocument, parser: MarkdownParser
    ) -> ExtractedCollaborationStyle | None:
        """抽取协作风格"""
        section_content = self._find_section_content(parser, "collaboration_style")
        if not section_content:
            return None

        # 尝试解析喜好和详情
        lines = section_content.split("\n")
        preference = ""
        details = ""

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if stripped.lower().startswith("preference:") or stripped.startswith("喜好:"):
                preference = stripped.split(":", 1)[1].strip() if ":" in stripped else stripped
            elif stripped.lower().startswith("details:") or stripped.startswith("详情:"):
                details = stripped.split(":", 1)[1].strip() if ":" in stripped else stripped
            elif not preference:
                preference = stripped
            else:
                details = details + " " + stripped if details else stripped

        if not preference:
            return None

        return ExtractedCollaborationStyle(
            preference=preference,
            details=details.strip() if details else None,
            confidence=0.8,
            source_ref=self._make_source_ref(doc, section="Collaboration Style"),
        )

    def _extract_skills_from_soul(
        self, doc: MarkdownDocument, parser: MarkdownParser
    ) -> list[ExtractedSkill]:
        """从 SOUL.md 抽取技能"""
        skills: list[ExtractedSkill] = []

        section_content = self._find_section_content(parser, "skills")
        if not section_content:
            return skills

        section_parser = MarkdownParser(section_content)
        lists = section_parser.get_lists()

        for lst in lists:
            for item in lst["items"]:
                if item.startswith("  "):
                    continue

                skill = self._parse_skill_item(item, doc)
                if skill:
                    skills.append(skill)

        return skills

    def _parse_skill_item(self, item: str, doc: MarkdownDocument) -> ExtractedSkill | None:
        """解析技能项，格式: "名称 (来源, 信任级别)" 或 "名称 (来源, 信任级别, approval_required)" """
        pattern = r"^(.+?)\s*\((.+?)\)\s*$"
        match = re.match(pattern, item.strip())

        if not match:
            # 简单名称格式
            name = item.strip()
            if name:
                return ExtractedSkill(
                    name=name,
                    skill_source=SkillSource.BUILTIN,
                    trust_level=TrustLevel.GUARDED,
                    approval_required=False,
                    confidence=0.6,
                    source_ref=self._make_source_ref(doc, section="Skills", snippet=item),
                )
            return None

        name = match.group(1).strip()
        params_str = match.group(2).lower()

        # 解析参数
        params = [p.strip() for p in params_str.split(",")]

        skill_source = SkillSource.BUILTIN
        trust_level = TrustLevel.GUARDED
        approval_required = False

        for param in params:
            if param in self.SKILL_SOURCES:
                skill_source = self.SKILL_SOURCES[param]
            elif param in self.TRUST_LEVELS:
                trust_level = self.TRUST_LEVELS[param]
            elif "approval" in param or "审批" in param:
                approval_required = True

        return ExtractedSkill(
            name=name,
            skill_source=skill_source,
            trust_level=trust_level,
            approval_required=approval_required,
            confidence=0.9,
            source_ref=self._make_source_ref(doc, section="Skills", snippet=item),
        )

    def _extract_resources_from_soul(
        self, doc: MarkdownDocument, parser: MarkdownParser
    ) -> list[ExtractedResource]:
        """从 SOUL.md 抽取资源"""
        resources: list[ExtractedResource] = []

        section_content = self._find_section_content(parser, "resources")
        if not section_content:
            return resources

        section_parser = MarkdownParser(section_content)
        lists = section_parser.get_lists()

        for lst in lists:
            for item in lst["items"]:
                if item.startswith("  "):
                    continue

                resource = self._parse_resource_item(item, doc)
                if resource:
                    resources.append(resource)

        return resources

    def _parse_resource_item(self, item: str, doc: MarkdownDocument) -> ExtractedResource | None:
        """解析资源项，格式: "名称 (类型, 权限)" """
        pattern = r"^(.+?)\s*\((.+?)\)\s*$"
        match = re.match(pattern, item.strip())

        if not match:
            name = item.strip()
            if name:
                return ExtractedResource(
                    id=f"res_{name.lower().replace(' ', '_')}",
                    name=name,
                    kind=ResourceKind.FILE,
                    access=ResourceAccess.READ,
                    confidence=0.5,
                    source_ref=self._make_source_ref(doc, section="Resources", snippet=item),
                )
            return None

        name = match.group(1).strip()
        params_str = match.group(2).lower()
        params = [p.strip() for p in params_str.split(",")]

        kind = ResourceKind.FILE
        access = ResourceAccess.READ

        for param in params:
            if param in self.RESOURCE_KINDS:
                kind = self.RESOURCE_KINDS[param]
            elif param in self.RESOURCE_ACCESS:
                access = self.RESOURCE_ACCESS[param]

        return ExtractedResource(
            id=f"res_{name.lower().replace(' ', '_')}",
            name=name,
            kind=kind,
            access=access,
            confidence=0.8,
            source_ref=self._make_source_ref(doc, section="Resources", snippet=item),
        )

    # =========================================================================
    # RULES.md 抽取
    # =========================================================================

    def _extract_from_rules(
        self, doc: MarkdownDocument, parser: MarkdownParser
    ) -> WorkerProfileExtractionResult:
        """从 RULES.md 抽取"""
        result = WorkerProfileExtractionResult(worker_id="")

        # 抽取约束
        constraints = self._extract_constraints(doc, parser)
        result.constraints.extend(constraints)

        # 抽取上报触发点
        triggers = self._extract_escalation_triggers(doc, parser)
        result.escalation_triggers.extend(triggers)

        return result

    def _extract_constraints(
        self, doc: MarkdownDocument, parser: MarkdownParser
    ) -> list[ExtractedConstraint]:
        """抽取约束"""
        constraints: list[ExtractedConstraint] = []

        section_content = self._find_section_content(parser, "constraints")
        if not section_content:
            return constraints

        section_parser = MarkdownParser(section_content)
        lists = section_parser.get_lists()

        for lst in lists:
            for item in lst["items"]:
                if item.startswith("  "):
                    continue

                constraint = self._parse_constraint_item(item, doc)
                if constraint:
                    constraints.append(constraint)

        return constraints

    def _parse_constraint_item(self, item: str, doc: MarkdownDocument) -> ExtractedConstraint | None:
        """解析约束项"""
        rule = item.strip()
        if not rule:
            return None

        # 确定策略
        rule_lower = rule.lower()

        if rule_lower.startswith("禁止") or "禁止" in rule_lower or "forbidden" in rule_lower:
            policy = ConstraintPolicy.FORBIDDEN
            severity = "high"
        elif "必须审批" in rule or "需要审批" in rule or "approval" in rule_lower:
            policy = ConstraintPolicy.APPROVAL_REQUIRED
            severity = "critical"
        elif rule_lower.startswith("可") or "允许" in rule or "allowed" in rule_lower:
            policy = ConstraintPolicy.ALLOWED
            severity = "low"
        else:
            # 默认为策略约束
            policy = ConstraintPolicy.ALLOWED
            severity = "medium"

        return ExtractedConstraint(
            kind=ConstraintKind.POLICY if policy == ConstraintPolicy.FORBIDDEN else ConstraintKind.APPROVAL if policy == ConstraintPolicy.APPROVAL_REQUIRED else ConstraintKind.SCOPE,
            rule=rule,
            policy=policy,
            severity=severity,
            confidence=0.9,
            source_ref=self._make_source_ref(doc, section="Constraints", snippet=item),
        )

    def _extract_escalation_triggers(
        self, doc: MarkdownDocument, parser: MarkdownParser
    ) -> list[ExtractedEscalationTrigger]:
        """抽取上报触发点"""
        triggers: list[ExtractedEscalationTrigger] = []

        section_content = self._find_section_content(parser, "escalation_triggers")
        if not section_content:
            return triggers

        section_parser = MarkdownParser(section_content)
        lists = section_parser.get_lists()

        for lst in lists:
            for item in lst["items"]:
                if item.startswith("  "):
                    continue

                trigger = self._parse_escalation_item(item, doc)
                if trigger:
                    triggers.append(trigger)

        return triggers

    def _parse_escalation_item(self, item: str, doc: MarkdownDocument) -> ExtractedEscalationTrigger | None:
        """解析上报触发点项，格式: "条件时上报给目标" 或 "条件 -> 上报目标" """
        rule = item.strip()
        if not rule:
            return None

        # 尝试解析条件和动作
        condition = ""
        action = ""

        # 尝试多种分隔符
        separators = ["时上报给", "时上报", "->", "→", "：上报给", ": escalate to"]
        for sep in separators:
            if sep in rule:
                parts = rule.split(sep, 1)
                condition = parts[0].strip()
                action = parts[1].strip() if len(parts) > 1 else ""
                break

        if not condition:
            # 无法解析，整体作为条件
            condition = rule
            action = "上报"  # 默认动作

        return ExtractedEscalationTrigger(
            condition=condition,
            action=action,
            confidence=0.8 if action else 0.6,
            source_ref=self._make_source_ref(doc, section="Escalation Triggers", snippet=item),
        )

    # =========================================================================
    # MEMORY.md 抽取
    # =========================================================================

    def _extract_from_memory(
        self, doc: MarkdownDocument, parser: MarkdownParser
    ) -> WorkerProfileExtractionResult:
        """从 MEMORY.md 抽取"""
        result = WorkerProfileExtractionResult(worker_id="")

        episodes = self._extract_memory_episodes(doc, parser)
        result.memory_episodes.extend(episodes)

        return result

    def _extract_memory_episodes(
        self, doc: MarkdownDocument, parser: MarkdownParser
    ) -> list[ExtractedMemoryEpisode]:
        """抽取记忆片段"""
        episodes: list[ExtractedMemoryEpisode] = []

        # 日期格式: YYYY-MM-DD 或 YYYY/MM/DD
        # 注意：heading['text'] 已经去除了 # 前缀
        date_pattern = re.compile(r"^(\d{4}[-/]\d{2}[-/]\d{2})")

        headings = parser.get_headings()
        all_lines = doc.content.split("\n")

        # 找到每个日期标题下的内容
        for i, heading in enumerate(headings):
            match = date_pattern.match(heading["text"])
            if match:
                timestamp = match.group(1).replace("/", "-")
                start_line = heading["line"]

                # 确定结束行
                end_line = len(all_lines)
                for j in range(i + 1, len(headings)):
                    if headings[j]["line"] > start_line:
                        end_line = headings[j]["line"] - 1
                        break

                # 提取该节的内容
                # heading['line'] 是 1-indexed，表示标题所在行号
                # all_lines 是 0-indexed，所以标题在 all_lines[start_line - 1]
                # 内容从下一行开始，即 all_lines[start_line]
                section_lines = all_lines[start_line:end_line]
                content = "\n".join(line.strip() for line in section_lines if line.strip())

                if content:
                    episodes.append(ExtractedMemoryEpisode(
                        timestamp=timestamp,
                        summary=content[:500],  # 限制摘要长度
                        confidence=0.85,
                        source_ref=self._make_source_ref(
                            doc,
                            heading=heading["text"],
                            line_start=start_line,
                            line_end=end_line,
                            snippet=content[:100],
                        ),
                    ))

        return episodes

    # =========================================================================
    # 辅助方法
    # =========================================================================

    def _find_section_content(self, parser: MarkdownParser, section_key: str) -> str:
        """查找节内容"""
        aliases = self.SECTION_ALIASES.get(section_key, [section_key])

        for alias in aliases:
            content = parser.get_section(alias)
            if content:
                return content

        return ""

    def _convert_skill_metadata(self, skill: SkillMetadataInput) -> ExtractedSkill | None:
        """转换技能元数据为抽取技能"""
        return ExtractedSkill(
            name=skill.name,
            skill_source=skill.source,
            trust_level=skill.trust_level,
            approval_required=skill.approval_required,
            confidence=1.0,  # 来自元数据，置信度最高
            source_ref=SourceReference(
                doc_type=DocType.SOUL,  # 技能元数据通常来自 SOUL
                doc_name="skill_metadata",
                section="skills",
            ),
        )

    def _convert_resource_metadata(self, resource: ResourceMetadataInput) -> ExtractedResource | None:
        """转换资源元数据为抽取资源"""
        return ExtractedResource(
            id=resource.id,
            name=resource.name,
            kind=resource.kind,
            access=resource.access,
            confidence=1.0,  # 来自元数据，置信度最高
            source_ref=SourceReference(
                doc_type=DocType.SOUL,  # 资源元数据通常来自 SOUL
                doc_name="resource_metadata",
                section="resources",
            ),
        )

    def _add_missing_field_warnings(
        self, result: WorkerProfileExtractionResult, input_data: ProfilingInput
    ) -> WorkerProfileExtractionResult:
        """添加缺失字段的警告"""
        # 检查关键缺失字段
        if not result.capabilities:
            result.warnings.append(ExtractionWarning(
                field="capabilities",
                message="文档中未找到能力描述 (Capabilities 节缺失或为空)",
                doc_type=DocType.SOUL,
                doc_name="SOUL.md",
                suggestion="请在 SOUL.md 中添加 Capabilities 节，格式：- 能力名称 (级别)",
            ))

        if not result.responsibilities:
            result.warnings.append(ExtractionWarning(
                field="responsibilities",
                message="文档中未找到职责描述 (Responsibilities 节缺失或为空)",
                doc_type=DocType.SOUL,
                doc_name="SOUL.md",
                suggestion="请在 SOUL.md 中添加 Responsibilities 节",
            ))

        # 检查是否有 RULES.md 但没有约束
        has_rules = input_data.has_document_type(DocType.RULES)
        if has_rules and not result.constraints:
            result.warnings.append(ExtractionWarning(
                field="constraints",
                message="RULES.md 存在但未抽取到约束条件",
                doc_type=DocType.RULES,
                doc_name="RULES.md",
                suggestion="请检查 RULES.md 中是否有 Constraints 节及有效的约束规则",
            ))

        return result


__all__ = ["BaselineProfileExtractor"]