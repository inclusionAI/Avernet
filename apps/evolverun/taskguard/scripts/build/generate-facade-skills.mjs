#!/usr/bin/env node
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const SKILLS_DIR = join(ROOT, "skills");

// ──── Generate static "workflow" skill (for workflow_choice tool) ────
// This skill teaches the Agent about the workflow_choice tool so it can
// interpret user intent when L1 keyword matching does not fire.
{
  mkdirSync(SKILLS_DIR, { recursive: true });

  const command = "workflow";
  const skillDir = join(SKILLS_DIR, command);
  mkdirSync(skillDir, { recursive: true });

  const lines = [
    "---",
    "name: workflow",
    "description: 工作流引擎核心命令。管理工作流生命周期（创建、确认、拒绝、跳过、重试、提交、恢复），以及在人工节点等待时通过 workflow_choice 工具解读用户自然语言意图。",
    "user-invocable: true",
    "command-dispatch: tool",
    "command-tool: workflow_engine_dispatch",
    "command-arg-mode: raw",
    "---",
    "",
    "# 工作流引擎核心命令",
    "",
    "管理工作流生命周期，支持以下子命令：",
    "",
    "## 子命令",
    "",
    "| 子命令 | 说明 | 示例 |",
    "|--------|------|------|",
    "| `run` | 启动工作流 | `/workflow run <workflow-id>` |",
    "| `confirm` | 确认人工节点（可带 choice） | `/workflow confirm --flow-id <id>` |",
    "| `confirm choice: <value>` | 选择分支并确认 | `/workflow confirm choice: fast --flow-id <id>` |",
    "| `reject` | 拒绝当前节点 | `/workflow reject --flow-id <id>` |",
    "| `skip` | 跳过当前节点 | `/workflow skip --flow-id <id>` |",
    "| `retry` | 重试当前节点 | `/workflow retry --flow-id <id>` |",
    "| `revise` | 修改并重新提交 | `/workflow revise --flow-id <id>` |",
    "| `submit` | 提交表单数据 | `/workflow submit --flow-id <id>` |",
    "| `resume` | 恢复挂起的工作流 | `/workflow resume --flow-id <id>` |",
    "",
    "## workflow_choice 工具",
    "",
    "当人工节点处于 waiting 状态且 L1 关键词匹配未命中时，Agent 应使用 **workflow_choice** 工具解读用户自然语言意图。",
    "",
    "### 参数",
    "",
    "- `flowId` (string, required): 等待中的工作流 ID",
    "- `choice` (string, required): 解读后的选择值，必须匹配 inputSchema 中的 enum 值",
    "- `reason` (string, optional): 选择原因或用户附加说明",
    "",
    "### 使用时机",
    "",
    "1. 工作流有人工节点处于 waiting 状态",
    "2. 用户发送了自然语言消息（如「批准」「快速处理」「我选深入分析」）",
    "3. L1 Hook 关键词匹配未命中（inputSchema 无 keywordAliases 或关键词未匹配）",
    "4. Agent 需要解读用户意图并调用 workflow_choice 完成选择",
    "",
    "### 示例",
    "",
    "- 用户说「批准」→ `workflow_choice(flowId=\"...\", choice=\"approve\")`",
    "- 用户说「我选方案B」→ `workflow_choice(flowId=\"...\", choice=\"thorough\")`",
    "- 用户说「算了不要了」→ 调用 `/workflow reject --flow-id <id>`",
    "",
    "## 意图识别规则",
    "",
    "- 用户输入 `/workflow <子命令>` → 调用 workflow_engine_dispatch 工具，command 参数为原始输入",
    "- 用户在等待中工作流上下文说自然语言 → 先尝试 L1 关键词匹配，未命中则使用 workflow_choice 工具",
    "",
  ];

  writeFileSync(join(skillDir, "SKILL.md"), lines.join("\n"), "utf-8");
  console.log("[generate-facade-skills] Generated skills/workflow/SKILL.md");
}

console.log("[generate-facade-skills] Done.");