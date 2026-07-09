"""PatchPlanner — 从 FindingsReport 生成可执行补丁定义并持久化.

职责：诊断报告 → PatchTemplate 查找 → LLM 生成修复内容 → PatchRecord 写入.

模型约束：
  - PatchOperation 不变，op 可以为 "llm_fix"（template 字段放修复指令）.
  - PatchRecord 的 preview_diff 放修复后的全文，backup_content 放修改前内容.
"""
from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.harness.models import (
    FindingsReport,
    Finding,
    PatchDefinition,
    PatchOperation,
    PatchRecord,
    PatchStatus,
    PatchTarget,
    PatchTemplate,
)
from agentclaw.community.core.harness.services.bot_profile import BotProfile
from agentclaw.community.core.harness.services.llm import LLM
from agentclaw.community.core.harness.services.patch_library import PatchLibrary
from agentclaw.community.core.harness.repository_protocol import (
    HarnessPatchRecordRepository,
    HarnessPatchRepository,
    HarnessScanRecordRepository,
)


def _align_trailing_newline(dst: str, src: str) -> str:
    """Ensure dst trailing newline matches src's."""
    src_has_final = src.endswith("\n")
    dst_has_final = dst.endswith("\n")
    if src_has_final and not dst_has_final:
        return dst + "\n"
    if not src_has_final and dst_has_final:
        return dst.rstrip("\n")
    return dst


class PatchPlanner:
    """FindingsReport → PatchRecord[].

    规则引擎只负责发现问题，PatchPlanner 负责：
      1. 按 suggested_template_ids 查 PatchLibrary
      2. 按模板聚合多条 Finding（同一模板只生成一个补丁）
      3. 调用 LLM 生成修复后的文件全文
      4. 创建 PatchRecord（含 preview_diff + backup_content）并写入 DB
    """

    def __init__(
        self,
        patch_library: PatchLibrary,
        llm: LLM,
        bot_profile: BotProfile,
        patch_record_repo: HarnessPatchRecordRepository,
        patch_repo: HarnessPatchRepository,
        scan_record_repo: HarnessScanRecordRepository,
    ):
        self._lib = patch_library
        self._llm = llm
        self._bot_profile = bot_profile
        self._patch_record_repo = patch_record_repo
        self._patch_repo = patch_repo
        self._scan_record_repo = scan_record_repo

    async def generate_and_save_patches(
        self,
        report: FindingsReport,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        operator_id: str = "",
        scan_id: int | None = None,
        publish_id: str | None = None,
    ) -> list[PatchRecord]:
        """从诊断报告自动聚合 findings、调用 LLM 生成修复内容并写入 patch_record 表.

        聚合规则：按 suggested_template_ids 分组，同一模板下多个 Finding 合并为一条 patch。
        """
        import logging
        logger = logging.getLogger(__name__)

        # ── 1. 按模板 ID 聚合 finding ─────────────────────────────
        template_id_to_findings: dict[int, list[Finding]] = {}
        for finding in report.findings:
            for tpl_id in finding.suggested_template_ids:
                template_id_to_findings.setdefault(tpl_id, []).append(finding)

        if not template_id_to_findings:
            logger.info(
                "[PatchPlanner] 没有 finding 关联到模板 (suggested_template_ids 为空)，跳过补丁生成"
            )
            return []

        logger.info(
            "[PatchPlanner] 需要处理的模板 IDs: %s",
            list(template_id_to_findings.keys()),
        )

        records: list[PatchRecord] = []
        # 记录每个 finding 对应的 patch_id（用于 enrich findings）
        finding_to_patch_id: dict[str, int] = {}

        for tpl_id, findings in template_id_to_findings.items():
            tpl = self._lib.get_template_by_id(tpl_id)
            if tpl is None:
                logger.warning(
                    "[PatchPlanner] 模板 ID %r 在 PatchLibrary 中不存在，跳过", tpl_id,
                )
                continue
            if not tpl.operations:
                logger.info("[PatchPlanner] 模板 %s (ID=%s) 无操作，跳过", tpl.name, tpl_id)
                continue

            # 只处理包含 llm_fix 或 update_md 操作的模板
            work_ops: list[PatchOperation] = [
                op for op in tpl.operations
                if op.op in ("llm_fix", "update_md", "update_file")
            ]
            if not work_ops:
                continue

            # 调用 LLM 生成修复内容，得到处理后的 operations
            processed = await self._generate_patch_operations(
                tpl=tpl,
                work_ops=work_ops,
                findings=findings,
                entity_type=entity_type,
                entity_id=entity_id,
                bot_id=bot_id,
                operator_id=operator_id,
                publish_id=publish_id,
            )
            if not processed:
                continue

            processed_ops, original_content, short_description = processed

            # 1. 使用处理后的 operations 写入 ac_harness_patch
            preview_diff = processed_ops[0].detail.get("dst_content") if processed_ops else None

            patch_def = PatchDefinition(
                template_id=tpl.id or 0,
                name=tpl.description or tpl.name,
                layer=tpl.layer,
                description=short_description,
                scope="bot",
                scope_value=report.bot_id,
                content=json.dumps(
                    [self._op_to_dict(op) for op in processed_ops],
                    ensure_ascii=False,
                ),
                scan_id=scan_id,
                is_applied=False,
                env=report.env,
            )
            patch_id = 0
            try:
                patch_id = self._patch_repo.create(patch_def)
            except Exception as e:
                logger.warning("[PatchPlanner] 写入 ac_harness_patch 失败: %s", e)

            # 记录该 template 下所有 findings 对应的 patch_id
            if patch_id:
                for f in findings:
                    finding_to_patch_id[f.rule_id] = patch_id

            # 2. 创建并写入 PatchRecord
            record = self._create_patch_record(
                bot_id=bot_id,
                entity_id=entity_id,
                patch_id=patch_id,
                tpl=tpl,
                target_files=sorted({op.target for op in work_ops}),
                processed_ops=processed_ops,
                preview_diff=preview_diff,
                original_content=original_content,
                bot_publish_id=publish_id,
            )
            if record:
                records.append(record)

        # 3. 更新 scan_record: patch_ids 和 enriched findings
        all_patch_ids = list(dict.fromkeys(finding_to_patch_id.values()))
        if all_patch_ids and scan_id:
            try:
                enriched_findings = self._enrich_findings_with_patch_ids(
                    report.findings, finding_to_patch_id
                )
                self._scan_record_repo.update_patch_ids(
                    scan_id=scan_id,
                    patch_ids=all_patch_ids,
                    findings_with_patch_ids=json.dumps(enriched_findings, ensure_ascii=False, default=str),
                )
                logger.info(
                    "[PatchPlanner] 更新 scan_id=%s 的 patch_ids=%s", scan_id, all_patch_ids
                )
            except Exception as e:
                logger.warning("[PatchPlanner] 更新 scan_record patch_ids 失败: %s", e)

        return records

    async def _generate_patch_operations(
        self,
        tpl: PatchTemplate,
        work_ops: list[PatchOperation],
        findings: list[Finding],
        entity_type: str,
        entity_id: str,
        bot_id: str,
        operator_id: str,
        publish_id: str | None = None,
    ) -> tuple[list[PatchOperation], str, str] | None:
        """为一个模板调用 LLM 生成修复内容，返回处理后的 operations、原始文件内容和简短描述."""
        import logging
        import copy
        logger = logging.getLogger(__name__)

        # 收集该模板涉及的所有文件（去重）
        target_files = sorted({op.target for op in work_ops})
        if not target_files:
            return None

        # 这里取第一个文件作为 primary target（单文件补丁，后续可扩展）
        primary_file = target_files[0]

        # 读取原始文件内容
        try:
            ref = await self._bot_profile.read_file(
                entity_type, entity_id, bot_id, primary_file, operator_id,
                publish_id=publish_id,
            )
            original_content = ref.content
        except Exception as e:
            logger.warning("[PatchPlanner] 读取文件 %s 失败: %s", primary_file, e)
            return None

        # 收集所有 finding 的问题描述
        issues_text = "\n\n".join(
            f"- [{f.severity.value}] {f.rule_name}: {f.message}"
            for f in findings
        )

        # 逐个 operation 填充 detail 并调用 LLM 生成 dst_content
        processed_ops: list[PatchOperation] = []
        for op in work_ops:
            # clone op so we don't mutate the in-memory template
            new_op = copy.copy(op)
            if new_op.detail is None:
                new_op.detail = {}

            if op.op == "update_md":
                result = await self._llm_generate_fix(
                    file_type=primary_file,
                    src=original_content,
                    issues=issues_text,
                    template_instructions=op.template or "",
                )
                if result is None:
                    logger.warning("[PatchPlanner] 模板 %s 生成 dst_content 失败", tpl.name)
                    continue
                dst, short_desc = result
                dst = _align_trailing_newline(dst, original_content)
                new_op.detail["src_content"] = original_content
                new_op.detail["dst_content"] = dst
            elif op.op == "llm_fix":
                result = await self._llm_generate_fix(
                    file_type=primary_file,
                    src=original_content,
                    issues=issues_text,
                    template_instructions=op.template or "",
                )
                if result is None:
                    continue
                llm_result, short_desc = result
                llm_result = _align_trailing_newline(llm_result, original_content)
                new_op.detail["src_content"] = original_content
                new_op.detail["dst_content"] = llm_result

            processed_ops.append(new_op)

        if not processed_ops:
            logger.info("[PatchPlanner] 模板 %s 没有成功生成任何操作，跳过", tpl.name)
            return None

        # 使用第一个 operation 生成的简短描述（通常只有一个）
        return processed_ops, original_content, short_desc

    def _create_patch_record(
        self,
        bot_id: str,
        entity_id: str,
        patch_id: int,
        tpl: PatchTemplate,
        target_files: list[str],
        processed_ops: list[PatchOperation],
        preview_diff: str | None,
        original_content: str,
        bot_publish_id: str | None = None,
    ) -> PatchRecord | None:
        """创建并持久化 PatchRecord."""
        import logging
        logger = logging.getLogger(__name__)

        primary_file = target_files[0] if target_files else ""

        record = PatchRecord(
            bot_id=bot_id,
            entity_id=entity_id,
            patch_id=patch_id,
            layer=tpl.layer,
            target=PatchTarget(files=target_files),
            status=PatchStatus.PLANNED,
            operations=processed_ops,
            preview_diff=preview_diff,
            backup_content=original_content,
            backup_file_types=target_files,
            bot_publish_id=bot_publish_id,
        )
        record.backup_checksum = record.compute_checksum(original_content)

        # 写入 DB
        try:
            self._patch_record_repo.create(record)
            logger.info(
                "[PatchPlanner] 已生成补丁记录 record_id=%s bot=%s file=%s",
                record.id, bot_id, primary_file,
            )
        except Exception as e:
            logger.warning("[PatchPlanner] 写入 patch_record 失败: %s", e)
            return None

        return record

    def _enrich_findings_with_patch_ids(
        self,
        findings: list[Finding],
        finding_to_patch_id: dict[str, int],
    ) -> list[dict]:
        """为每个 finding 添加生成的 patch_id，返回 grouped JSON 结构.

        Grouped format:
        [
          {
            "check_item": "AGENTS.md",
            "all_patch_id_list": [300001],
            "finding_details": [
              {
                "name": "...",
                "message": "...",
                "risk_level": "critical",
                "suggested_template_ids": [],
                "patch_id_list": [300001]
              }
            ]
          }
        ]
        """
        from agentclaw.community.core.harness.models import Severity

        # 按 file_type 分组
        groups: dict[str, list[Finding]] = {}
        for f in findings:
            ft = f.file_type or "_unknown"
            if ft not in groups:
                groups[ft] = []
            groups[ft].append(f)

        result = []
        for ft, flist in groups.items():
            details = []
            all_patch_ids: list[int] = []
            for f in flist:
                patch_id = finding_to_patch_id.get(f.rule_id)
                risk = f.severity.value if isinstance(f.severity, Severity) else str(f.severity)
                detail = {
                    "name": f.rule_name,
                    "message": f.message,
                    "risk_level": risk,
                    "suggested_template_ids": [],
                    "patch_id_list": [patch_id] if patch_id else [],
                }
                details.append(detail)
                if patch_id and patch_id not in all_patch_ids:
                    all_patch_ids.append(patch_id)

            result.append({
                "check_item": ft,
                "all_patch_id_list": all_patch_ids,
                "finding_details": details,
            })

        return result

    async def _llm_generate_fix(
        self,
        file_type: str,
        src: str,
        issues: str,
        template_instructions: str,
    ) -> tuple[str, str] | None:
        """调用 LLM 生成修复内容，同时返回修复后文件和简短描述.

        Returns:
            (修复后文件内容, 简短修复描述) 或 None（如果失败）
        """
        import logging
        import re
        logger = logging.getLogger(__name__)

        system_prompt = self._build_system_prompt_with_summary(file_type)
        user_prompt = self._build_user_prompt_with_summary(
            file_type=file_type,
            original_content=src,
            issues=issues,
            template_instructions=template_instructions,
        )
        try:
            result = await self._llm.chat(system=system_prompt, user=user_prompt)
        except Exception as e:
            logger.warning("[PatchPlanner] LLM chat failed: %s", e)
            return None
        if not result:
            logger.warning("[PatchPlanner] LLM returned empty content")
            return None
        if result.strip() == "[llm disabled]":
            logger.warning("[PatchPlanner] LLM disabled")
            # Fallback: 返回原始内容和一个简单描述
            issues_list = issues.split("\n")[:2]  # 取前2个问题
            short_desc = "修复: " + "; ".join(
                line.strip("- ")[:40] for line in issues_list if line
            )
            if len(short_desc) > 100:
                short_desc = short_desc[:97] + "..."
            return src, short_desc

        # 解析结果：分离 FIX_SUMMARY 和文件内容
        summary_match = re.search(r'<!-- FIX_SUMMARY:\s*(.+?)\s*-->', result, re.DOTALL)
        short_description = ""
        if summary_match:
            short_description = summary_match.group(1).strip()
            # 从结果中移除 FIX_SUMMARY 标记
            fixed_content = re.sub(r'<!-- FIX_SUMMARY:\s*.+?\s*-->', '', result, flags=re.DOTALL).strip()
        else:
            fixed_content = result

        fixed_content = self._strip_code_fences(fixed_content)
        short_description = short_description[:100] if len(short_description) <= 100 else short_description[:97] + "..."

        if fixed_content.strip() == src.strip():
            logger.warning("[PatchPlanner] LLM fixed content same as original, skipping")
            return None

        return fixed_content, short_description

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        import re
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:markdown|md)?\s*\n?", "", stripped)
            stripped = re.sub(r"\n?```\s*$", "", stripped)
        return stripped.strip()

    @staticmethod
    def _build_system_prompt_with_summary(file_type: str) -> str:
        return f"""你是一个 Bot 配置文件修复专家。请根据提供的问题诊断结果，生成修复后的 {file_type} 文件完整内容。

要求：
1. 输出必须是修复后的文件**完整内容**。
2. 在文件开头添加一行注释，格式：`<!-- FIX_SUMMARY: 简短修复描述（不超过100字） -->`
3. 注释之后的所有内容必须是文件本身的完整内容，不要有任何解释性文字。
4. 确保修复后的文件是格式正确的 Markdown。
5. 必须保留文件原有的合法结构和信息，只在有问题的部分做修改。
6. **严格基于建议事实进行修改**：只能根据【诊断发现的问题】和【修复指导】中明确提供的内容进行修复，禁止自行编造、补充或推测任何未在建议中提供的信息（如MCP业务语义、具体参数值、示例内容等）。
7. 如果建议中要求补充某类信息但没有提供具体内容，用 `<!-- TODO: 描述需要补充的内容 -->` 占位标记代替，而不是保持原有内容不变。确保每个被诊断出的问题都有对应的修改。
8. 修复应当最小化：只修改与问题直接相关的部分，保留其他所有原始内容。
9. **禁止原样返回**：输出必须与原始文件有所区别，否则视为修复失败。

输出示例：
```
<!-- FIX_SUMMARY: 补充了AGENTS.md缺失的bot基础信息字段 -->

# Bot Configuration
name: MyBot
...
```"""

    @staticmethod
    def _build_user_prompt_with_summary(
        file_type: str,
        original_content: str,
        issues: str,
        template_instructions: str,
    ) -> str:
        parts = [
            f"--- 目标文件: {file_type} ---",
            "",
            "【原始文件内容】",
            original_content,
            "",
            "【诊断发现的问题】",
            issues,
            "",
        ]
        if template_instructions:
            parts.extend([
                "【修复指导】",
                template_instructions,
                "",
            ])
        parts.append(
            "请根据以上信息，生成修复后的完整文件内容。\n"
            "第一行必须是 `<!-- FIX_SUMMARY: 简短描述 -->` 格式的注释，"
            "后面紧跟修复后的文件完整内容。\n\n"
            "重要约束：\n"
            "- 只能基于【诊断发现的问题】和【修复指导】中**明确提供的信息**进行修改\n"
            "- 如果建议要求补充某类信息但未提供具体内容，用 `<!-- TODO: 描述 -->` 占位标记代替，不要保持原内容不变\n"
            "- 禁止自行补充MCP业务语义、示例值、占位符等未在建议中明确给出的内容\n"
            "- 保持最小化修改原则：仅修复问题涉及的部分，其余内容原样保留\n"
            "- 输出必须与原始文件有所区别，禁止原样返回"
        )
        return "\n".join(parts)

    @staticmethod
    def _op_to_dict(op: PatchOperation) -> dict[str, Any]:
        return {
            "op": op.op,
            "target": op.target,
            "template": op.template,
            "detail": op.detail,
        }
