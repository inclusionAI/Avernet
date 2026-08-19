#!/usr/bin/env node
/**
 * sync-node-docs-to-yuque.mjs
 *
 * 自动提取 ClawMind 所有节点执行器的用法文档，并同步到语雀知识库。
 *
 * 功能：
 *   1. 从 src/types.ts 提取所有 NodeExecutor 类型定义及参数
 *   2. 从 src/index.ts 提取执行器分发逻辑
 *   3. 从 src/executors/ 提取执行器实现细节（默认值、校验逻辑等）
 *   4. 从 packs/ 扫描真实 YAML 配置示例
 *   5. 生成详细 Markdown 文档
 *   6. 通过语雀 API 同步到知识库（按节点类型分文档）
 *   7. 支持 --check 模式：检测代码变化，增量更新文档
 *
 * 用法：
 *   node scripts/sync-node-docs-to-yuque.mjs            # 全量同步
 *   node scripts/ssync-node-docs-to-yuque.mjs --check    # 检查变化后增量同步
 *   node scripts/sync-node-docs-to-yuque.mjs --dry-run   # 生成文档但不推送
 *   node scripts/sync-node-docs-to-yuque.mjs --node embedded-agent  # 只同步指定节点
 *
 * 环境变量：
 *   YUQUE_BOOK_ID        - 语雀知识库 ID（默认 248551688）
 *   YUQUE_TOKEN          - 语雀 API Token（通过 MCP 调用时不需要）
 */

import fs from "node:fs";
import path from "node:path";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// ── 配置 ──────────────────────────────────────────────────────────────────

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, "..");
const SRC_DIR = path.join(PROJECT_ROOT, "src");
const PACKS_DIR = path.join(PROJECT_ROOT, "packs");
const TYPES_FILE = path.join(SRC_DIR, "types.ts");
const INDEX_FILE = path.join(SRC_DIR, "index.ts");
const EXECUTORS_DIR = path.join(SRC_DIR, "executors");
const CACHE_FILE = path.join(PROJECT_ROOT, ".node-docs-cache.json");

const YUQUE_BOOK_ID = process.env.YUQUE_BOOK_ID || "248551688";

// ── 节点类型的完整文档元数据 ──────────────────────────────────────────────
// 从 types.ts 提取的类型定义，补充人工编写的描述和示例

const NODE_META = {
  "embedded-agent": {
    displayName: "Embedded Agent（内嵌智能体）",
    description: "在当前 OpenClaw 会话中内嵌运行 Agent，是最常用的节点类型。支持 LLM 推理、工具调用、JSON 结构化输出、Skill 加载、会话压缩等高级功能。",
    category: "智能体",
    executorFile: "embedded-agent.ts",
    yamlExample: null, // 从 packs 自动提取
  },
  subagent: {
    displayName: "Subagent（子智能体）",
    description: "启动一个独立的子 Agent 会话来执行任务。与 embedded-agent 的区别是 subagent 运行在隔离的 session 中，通过子 agent 运行后等待完成并收集结果。",
    category: "智能体",
    executorFile: "subagent.ts",
  },
  "cli-script": {
    displayName: "CLI Script（命令行脚本）",
    description: "执行系统命令行脚本，支持参数传递、环境变量注入和超时控制。常用于调用 Python/Shell 脚本执行数据处理、API 调用等确定性任务。",
    category: "执行器",
    executorFile: "cli-script.ts",
  },
  "mcp-call": {
    displayName: "MCP Call（MCP 工具调用）",
    description: "通过 mcporter 调用已注册的 MCP Server 上的工具，实现确定性工具调用（非 LLM 驱动）。",
    category: "执行器",
    executorFile: "mcp-call.ts",
  },
  "baas-call": {
    displayName: "BaaS Call（BaaS 服务调用）",
    description: "调用 BaaS（Bot as a Service）平台的 API，支持 run 和 message 两种模式，自动提交并轮询结果。",
    category: "执行器",
    executorFile: "baas-call.ts",
  },
  "bcs-route": {
    displayName: "BCS Route（BCS 路由）",
    description: "通过 BCS（Bot Collaboration Service）将消息路由给指定目标，用于跨会话协作场景。",
    category: "协作",
    executorFile: "bcs-route.ts",
  },
  "bcs-approval-batch": {
    displayName: "BCS Approval Batch（BCS 批量审批）",
    description: "批量发起 BCS 协作审批请求，一次性将多个审批节点合成一个批量请求发送。",
    category: "协作",
    executorFile: "bcs-approval-batch.ts",
  },
  approval: {
    displayName: "Approval（审批）",
    description: "审批节点，支持钉钉交互卡片、Web 审批页面等多种投递通道。可配置审批人、审批策略（any/all/majority）、跳过条件等。",
    category: "协作",
    executorFile: "approval-card-dingtalk.ts",
  },
  collaboration: {
    displayName: "Collaboration（协作）",
    description: "协作节点，结合 subagent 执行和 BCS 路由投递。在 private/dingtalk-group 模式下使用 subagent 运行，在 BCS 模式下使用 bcs-route 路由。",
    category: "协作",
    executorFile: null,
  },
  human: {
    displayName: "Human Wait（人工等待）",
    description: "暂停流程等待人工操作，支持确认/修改/拒绝三种动作，可配置输入 schema 和命令提示。",
    category: "人工",
    executorFile: "human-wait.ts",
  },
  done: {
    displayName: "Done（完成）",
    description: "标记流程完成的终态节点，不执行任何操作。",
    category: "控制",
    executorFile: null,
  },
  action: {
    displayName: "Action（动作）",
    description: "调用已注册的 Action（Python 脚本、模板解析、Hook 运行器等），适用于可复用的确定性操作。",
    category: "执行器",
    executorFile: null,
  },
  "loop-group": {
    displayName: "Loop Group（循环组）",
    description: "循环执行一组节点，直到满足退出条件或达到最大迭代次数。常用于迭代修复、多次校验等场景。",
    category: "控制",
    executorFile: null,
  },
  subworkflow: {
    displayName: "Subworkflow（子工作流）",
    description: "嵌套调用另一个工作流，支持参数传递、输出映射和失败策略。最大嵌套深度为 3 层。",
    category: "控制",
    executorFile: "subworkflow.ts",
  },
};

// ── 类型解析：从 types.ts 提取执行器参数 ──────────────────────────────────

function extractExecutorTypes(typesContent) {
  const types = {};

  // 匹配 type XxxExecutor = { ... };
  const typeRegex = /export type (\w+Executor)\s*=\s*\{([\s\S]*?)\};/g;
  let match;
  while ((match = typeRegex.exec(typesContent)) !== null) {
    const typeName = match[1];
    const body = match[2];
    const fields = [];

    // 解析字段：name?: type; 或 name: type;
    const fieldRegex = /(\w+)(\??):\s*([^;\n]+);/g;
    let fieldMatch;
    while ((fieldMatch = fieldRegex.exec(body)) !== null) {
      const name = fieldMatch[1];
      const optional = fieldMatch[2] === "?";
      const typeExpr = fieldMatch[3].trim();

      // 跳过 type 字段本身
      if (name === "type") continue;

      fields.push({ name, optional, type: typeExpr });
    }

    types[typeName] = fields;
  }

  return types;
}

function executorTypeToNodeType(typeName) {
  // EmbeddedAgentExecutor -> embedded-agent
  return typeName
    .replace("Executor", "")
    .replace(/([A-Z])/g, (m, p, offset) => (offset > 0 ? "-" : "") + m.toLowerCase());
}

// ── 从 executors/ 提取默认值和校验逻辑 ────────────────────────────────────

function extractDefaultsFromExecutor(filePath) {
  if (!fs.existsSync(filePath)) return {};

  const content = fs.readFileSync(filePath, "utf-8");
  const defaults = {};

  // 匹配 executor.xxx ?? defaultValue 模式
  const defaultRegex = /executor\.(\w+)\s*\?\?\s*([^,\n);]+)/g;
  let match;
  while ((match = defaultRegex.exec(content)) !== null) {
    defaults[match[1]] = match[2].trim();
  }

  // 匹配 executor.xxx ??= xxx 或 config.xxx ?? defaultValue
  const configDefaultRegex = /(?:executor|config)\.(\w+)\s*\?\?\s*([^,\n);]+)/g;
  while ((match = configDefaultRegex.exec(content)) !== null) {
    if (!defaults[match[1]]) {
      defaults[match[1]] = match[2].trim();
    }
  }

  return defaults;
}

// ── 从 packs/ 扫描 YAML 示例 ─────────────────────────────────────────────

function extractYamlExamples(packsDir, nodeType) {
  const examples = [];

  if (!fs.existsSync(packsDir)) return examples;

  const packDirs = fs.readdirSync(packsDir, { withFileTypes: true })
    .filter(d => d.isDirectory());

  for (const packDir of packDirs) {
    const workflowsDir = path.join(packsDir, packDir.name, "workflows");
    if (!fs.existsSync(workflowsDir)) continue;

    const yamlFiles = fs.readdirSync(workflowsDir).filter(f => f.endsWith(".yaml"));
    for (const yamlFile of yamlFiles) {
      const content = fs.readFileSync(path.join(workflowsDir, yamlFile), "utf-8");
      // 查找包含该节点类型的 YAML 片段
      const lines = content.split("\n");
      let inNode = false;
      let nodeLines = [];
      let nodeTitle = "";
      let nodeId = "";

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (line.includes(`type: ${nodeType}`) || (nodeType === "approval" && line.includes('type: approval'))) {
          // 向上查找 id 和 title
          for (let j = i - 1; j >= Math.max(0, i - 10); j--) {
            if (lines[j].match(/^\s+-\s+id:\s+/)) {
              nodeId = lines[j].trim().replace(/- id:\s+/, "");
            }
            if (lines[j].match(/^\s+title:\s+/)) {
              nodeTitle = lines[j].trim().replace(/title:\s+/, "");
            }
          }
          // 向下收集到下一个节点或文件结束
          inNode = true;
          nodeLines = [line];
          for (let k = i + 1; k < lines.length; k++) {
            if (lines[k].match(/^\s+-\s+id:\s+/) || (lines[k].trim() === "" && k + 1 < lines.length && lines[k + 1].match(/^\s+-\s+id:\s+/))) {
              break;
            }
            nodeLines.push(lines[k]);
          }
          if (nodeLines.length > 0) {
            examples.push({
              pack: packDir.name,
              workflow: yamlFile.replace(".yaml", ""),
              nodeId,
              nodeTitle,
              yaml: nodeLines.join("\n").trim(),
            });
          }
          break;
        }
      }
    }
  }

  return examples.slice(0, 5); // 每个节点类型最多 5 个示例
}

// ── 生成 Markdown 文档 ────────────────────────────────────────────────────

function generateNodeDoc(nodeType, meta, executorFields, defaults, yamlExamples) {
  const lines = [];

  lines.push(`# ${meta.displayName}`);
  lines.push("");
  lines.push(`> 节点类型：\`${nodeType}\` | 分类：${meta.category}`);
  lines.push("");
  lines.push(meta.description);
  lines.push("");

  // ── executor 参数表 ──
  lines.push("## executor 参数");
  lines.push("");
  lines.push("在 YAML 中通过 `executor` 字段配置：");
  lines.push("");
  lines.push("```yaml");
  lines.push("executor:");
  lines.push(`  type: ${nodeType}`);

  if (executorFields.length > 0) {
    for (const field of executorFields) {
      if (field.name === "type") continue;
      const defaultVal = defaults[field.name];
      const required = !field.optional;
      const comment = required ? "# 必填" : defaultVal ? `# 可选，默认 ${defaultVal}` : "# 可选";
      const value = field.optional ? (defaultVal || getDefaultForType(field.type)) : getExampleForType(field.name, field.type);
      if (field.type.includes("Record<string") || field.type.includes("Array<") || field.type.includes("WorkflowNode[")) {
        lines.push(`  ${field.name}: ${value}  ${comment}`);
      } else {
        lines.push(`  ${field.name}: ${value}  ${comment}`);
      }
    }
  }

  lines.push("```");
  lines.push("");

  // ── 参数详情 ──
  lines.push("### 参数说明");
  lines.push("");
  lines.push("| 参数 | 类型 | 必填 | 默认值 | 说明 |");
  lines.push("|------|------|------|--------|------|");

  for (const field of executorFields) {
    if (field.name === "type") continue;
    const required = !field.optional;
    const defaultVal = defaults[field.name] || "-";
    const description = getFieldDescription(nodeType, field.name);
    lines.push(`| \`${field.name}\` | ${formatTypeDisplay(field.type)} | ${required ? "✅" : "❌"} | ${defaultVal} | ${description} |`);
  }

  lines.push("");

  // ── 节点通用属性 ──
  lines.push("## 节点通用属性");
  lines.push("");
  lines.push("除 `executor` 外，每个节点还支持以下通用属性：");
  lines.push("");
  lines.push("| 属性 | 类型 | 必填 | 默认值 | 说明 |");
  lines.push("|------|------|------|--------|------|");
  lines.push("| `id` | string | ✅ | - | 节点唯一标识，同一工作流内不可重复 |");
  lines.push("| `title` | string | ✅ | - | 节点显示标题 |");
  lines.push("| `phase` | string | ✅ | - | 阶段标识（如 P1、P2），同 phase 节点可并行 |");
  lines.push("| `dependsOn` | string[] | ✅ | `[]` | 依赖的前置节点 ID 列表 |");
  lines.push("| `branchId` | string | ❌ | - | 分支标识，用于 onResult 条件分支 |");
  lines.push("| `triggerRule` | string | ❌ | `all_success` | 触发规则：`all_success` / `one_success` / `any_success` |");
  lines.push("| `retry` | object | ❌ | - | 重试配置，见下方「重试配置」 |");
  lines.push("| `outputContract` | object | ❌ | - | 输出契约，定义 JSON schema 约束输出格式 |");
  lines.push("| `onResult` | object | ❌ | - | 条件分支，根据节点输出决定后续流程 |");
  lines.push("| `progressMessage` | string | ❌ | - | 节点执行时的进度提示文本 |");
  lines.push("| `knowledge` | boolean | ❌ | false | 是否注入知识库内容作为上下文 |");
  lines.push("| `knowledgeBaseId` | string | ❌ | - | 指定 GRT 知识库配置（优先于 knowledge） |");
  lines.push("| `knowledgeQuery` | string | ❌ | - | 自定义知识库查询语句 |");
  lines.push("| `mock` | object | ❌ | - | Dry-run 测试的 Mock 配置 |");
  lines.push("| `validationTemplateId` | string | ❌ | - | 输出校验模板 ID |");
  lines.push("| `validationMinScore` | number | ❌ | 60 | 输出校验最低分数（0-100） |");
  lines.push("");

  // ── 重试配置 ──
  lines.push("## 重试配置");
  lines.push("");
  lines.push("通过 `retry` 字段配置节点执行失败后的重试策略：");
  lines.push("");
  lines.push("```yaml");
  lines.push("retry:");
  lines.push("  maxAttempts: 3        # 最大重试次数（含首次执行）");
  lines.push("  backoffMs: 3000       # 重试间隔（毫秒）");
  lines.push("  on: [executor-failed] # 触发重试的错误类型");
  lines.push("```");
  lines.push("");

  // ── 输出契约 ──
  lines.push("## 输出契约（outputContract）");
  lines.push("");
  lines.push("定义节点输出的 JSON Schema，用于约束 LLM 输出格式和自动校验：");
  lines.push("");
  lines.push("```yaml");
  lines.push("outputContract:");
  lines.push("  required: true");
  lines.push("  schema:");
  lines.push("    type: object");
  lines.push("    required: [fieldName1, fieldName2]");
  lines.push("    properties:");
  lines.push("      fieldName1:");
  lines.push("        type: string");
  lines.push("      fieldName2:");
  lines.push("        type: boolean");
  lines.push("```");
  lines.push("");

  // ── 条件分支 ──
  lines.push("## 条件分支（onResult）");
  lines.push("");
  lines.push("根据节点输出字段决定后续流程走向：");
  lines.push("");
  lines.push("```yaml");
  lines.push("# 简单条件：字段等于指定值时结束流程");
  lines.push("onResult:");
  lines.push("  if:");
  lines.push("    path: dataExists    # 输出字段路径");
  lines.push("    equals: false       # 期望值");
  lines.push("  then:");
  lines.push("    complete: true      # 提前结束流程");
  lines.push("");
  lines.push("# 多分支条件");
  lines.push("onResult:");
  lines.push("  branches:");
  lines.push("    - branchId: approved");
  lines.push("      match: { approved: true }");
  lines.push("      complete: false");
  lines.push("    - branchId: rejected");
  lines.push("      match: { approved: false }");
  lines.push("      complete: false");
  lines.push("  default:");
  lines.push("    complete: false");
  lines.push("```");
  lines.push("");

  // ── 特有功能 ──
  const specialContent = generateSpecialSection(nodeType);
  if (specialContent) {
    lines.push(specialContent);
  }

  // ── YAML 示例 ──
  if (yamlExamples.length > 0) {
    lines.push("## YAML 配置示例");
    lines.push("");

    for (const ex of yamlExamples) {
      lines.push(`### ${ex.nodeTitle || ex.nodeId}（来自 ${ex.pack}/${ex.workflow}）`);
      lines.push("");
      lines.push("```yaml");
      lines.push(ex.yaml);
      lines.push("```");
      lines.push("");
    }
  }

  // ── 注意事项 ──
  const notes = getNodeNotes(nodeType);
  if (notes.length > 0) {
    lines.push("## 注意事项");
    lines.push("");
    for (const note of notes) {
      lines.push(`- ${note}`);
    }
    lines.push("");
  }

  // ── 元信息 ──
  lines.push("---");
  lines.push("");
  lines.push(`*最后更新：${new Date().toISOString().split("T")[0]} | 由 sync-node-docs-to-yuque.mjs 自动生成*`);

  return lines.join("\n");
}

// ── 辅助函数 ──────────────────────────────────────────────────────────────

function getDefaultForType(type) {
  if (type.includes("string")) return '""';
  if (type.includes("number")) return "0";
  if (type.includes("boolean")) return "false";
  if (type.includes("Record<string")) return "{}";
  if (type.includes("Array<") || type.includes("[")) return "[]";
  return "null";
}

function getExampleForType(name, type) {
  const examples = {
    prompt: '"请分析以下数据并返回结果"',
    message: '"请审批以下申请"',
    command: '"python3 /path/to/script.py"',
    server: '"my-mcp-server"',
    tool: '"search"',
    args: '{ key: "value" }',
    skillName: '"my-skill"',
    target: '"审批Bot"',
    action: '"my-action"',
    workflowId: '"sub-workflow-id"',
    packId: '"my-pack"',
    botId: '"bot-123"',
    baseUrl: '"https://example.com/api"',
    iamToken: '"token-value"',
    cardId: '"card_0440e96c"',
    approvalType: '"BUDGET_APPROVE"',
    iterationVar: '"i"',
  };
  if (examples[name]) return examples[name];
  if (type.includes("string")) return '"value"';
  if (type.includes("number")) return "100";
  if (type.includes("boolean")) return "true";
  if (type.includes("Record<string, string>")) return "{ key: value }";
  if (type.includes("WorkflowApprover[]")) return "[{ empId: \"10001\", name: \"张三\" }]";
  if (type.includes("CardFieldDef[]")) return "[{ label: \"标题\", value: \"值\" }]";
  if (type.includes("WorkflowNode[]")) return "[]";
  if (type.includes("BcsRouteSpec")) return "{ to: [{ type: name, value: target }] }";
  if (type.includes("WorkflowContextPolicy")) return "{ history: structured }";
  return "null";
}

function formatTypeDisplay(type) {
  return type
    .replace(/"/g, "`")
    .replace(/'/g, "`")
    .replace(/\|/g, " \\| ");
}

function getFieldDescription(nodeType, fieldName) {
  const descriptions = {
    // embedded-agent
    skillName: "要加载的 Skill 名称，执行时自动查找并注入到 Agent 上下文",
    prompt: "发送给 Agent 的提示词模板，支持 {{var}} 变量插值",
    outputMode: "输出模式：`text` 返回纯文本，`json` 强制 JSON 输出并自动修复/校验",
    timeoutSeconds: "执行超时时间（秒），超时后节点失败",
    contextPolicy: "上下文策略：控制历史会话注入方式（structured/isolated/inherit/tail/compacted）",
    // subagent
    // cli-script
    command: "要执行的系统命令，支持 ~ 和 $HOME 路径展开",
    args: "命令参数：数组形式作为位置参数，对象形式转为 --key value 和 ARG_KEY 环境变量",
    env: "额外环境变量，注入到子进程环境中",
    // mcp-call
    server: "MCP Server 名称（在 OpenClaw 配置中注册的）",
    tool: "要调用的 MCP 工具名称",
    // baas-call
    mode: "调用模式：`run` 标准运行模式，`message` 消息模式（需 botId）",
    botId: "BaaS Bot ID（message 模式必填）",
    baseUrl: "BaaS API 基础 URL",
    iamToken: "IAM Token，用于 Cookie 认证（办公网络环境）",
    pollIntervalMs: "轮询间隔（毫秒），使用指数退避策略",
    // bcs-route
    target: "路由目标名称或 ID",
    // approval
    approvers: "审批人列表，每项包含 empId（工号）、name（姓名）、role（角色，可选）",
    cardFields: "审批卡片字段定义，每项包含 label（标签）和 value（值）",
    approvalPolicy: "审批策略：`any` 任一通过、`all` 全部通过、`majority` 多数通过",
    delivery: "投递通道配置，按执行模式分别指定 primary 通道",
    route: "BCS 路由配置，指定路由目标和原因",
    skipWhen: "跳过条件：当模板变量值匹配时自动通过，不发送审批卡片",
    saveAs: "持久化字段映射：回调结果写入 workflowData",
    cardTitle: "自定义卡片标题（支持 {{var}} 模板）",
    statusLabel: "自定义状态标签（支持 {{var}} 模板）",
    actionLabel: "自定义操作按钮文本（支持 {{var}} 模板）",
    workflowUrl: "自定义工单详情页 URL（支持 {{var}} 模板）",
    onRevise: "修改动作配置：指定反馈路径和目标节点",
    // collaboration
    taskKind: "协作任务类型标识",
    routeDisplayName: "路由显示名称",
    participant: "参与者配置",
    onFeedback: "反馈回调配置",
    // human
    waitKind: "等待类型标识，如 human-confirm、review-decision 等",
    inputSchema: "输入表单 Schema，定义用户需要填写的数据结构",
    actions: "动作配置：confirm（确认）、revise（修改）、reject（拒绝）",
    commandHints: "命令提示配置，定义各动作的触发命令",
    // done - no fields
    // action
    action: "要调用的已注册 Action ID",
    // loop-group
    maxIterations: "最大循环迭代次数",
    iterationVar: "迭代变量名，在循环体中可通过 {{iterationVar}} 引用当前迭代索引",
    until: "退出条件：当指定节点输出的指定路径等于指定值时退出循环",
    body: "循环体节点列表，每次迭代依次执行这些节点",
    onMaxIterations: "达到最大迭代次数时的策略：continue（使用最后一次结果）或 fail（标记失败）",
    // subworkflow
    workflowId: "要嵌套调用的子工作流 ID",
    packId: "子工作流所属 Pack ID（可选，默认在当前 Pack 中查找）",
    params: "传递给子工作流的参数，支持 {{var}} 模板",
    onFailure: "子工作流失败策略：fail（标记失败）、retry（重试）、skip（跳过）",
  };
  return descriptions[fieldName] || "";
}

function generateSpecialSection(nodeType) {
  const sections = {
    "embedded-agent": `## 特有功能

### JSON 输出模式（outputMode: json）

当设置 \`outputMode: json\` 时，embedded-agent 会：
1. 在 prompt 末尾追加 JSON 输出指令
2. 尝试解析模型输出为 JSON 对象
3. 如果解析失败，先尝试轻量修复（去除 Markdown 围栏、尾部逗号等）
4. 如果修复仍失败，自动发起一次 LLM 修复请求
5. 校验 \`{success: false}\` 或 \`{status: "FAILED"}\` 等错误指示字段

### Skill 加载

通过 \`skillName\` 指定要加载的 Skill，执行器会：
1. 在 Skill 目录中查找匹配的 Skill 文件
2. 将 Skill 目录和入口文件注入到 prompt
3. 过滤 session snapshot 只保留该 Skill 的内容（减少上下文噪音）

### 会话压缩

embedded-agent 支持自动会话压缩，避免长会话导致 token 超限：
- 运行前压缩：如果 session 文件超过阈值，自动压缩
- 运行中压缩：通过 OpenClaw 插件 Hook 在 agent loop 中持续压缩
- 限流重试前压缩：429 限流重试前也会压缩 session

### 限流保护

内置 LLM 并发信号量（默认 maxConcurrent=3），防止多个 workflow 实例同时发起 LLM 请求触发 429 限流。支持错峰启动（staggered start）和 429 自动重试（最多 3 次）。

### 输出契约联动

当节点同时定义了 \`outputContract\` 时，JSON 模式会自动启用，校验逻辑会使用 outputContract.schema 来指导 LLM 输出格式。

### 上下文策略

通过 \`contextPolicy\` 控制会话历史注入方式：
- \`structured\`：构建结构化 Workflow Context JSON（推荐）
- \`isolated\`：隔离模式，不继承任何历史
- \`inherit\`：继承完整父会话历史
- \`tail\`：只保留最近的对话
- \`compacted\`：使用压缩后的历史`,

    subagent: `## 特有功能

### 与 embedded-agent 的区别

| 特性 | subagent | embedded-agent |
|------|----------|----------------|
| 运行环境 | 独立子会话 | 当前会话内 |
| 上下文隔离 | 完全隔离 | 可配置隔离/继承 |
| Skill 支持 | ✅ 必须指定 | ✅ 可选 |
| JSON 输出 | 从消息中提取 | 原生支持 + 自动修复 |
| 上下文策略 | 支持 structured/isolated | 支持全部模式 |
| 降级机制 | 可降级为 embedded-agent | - |

### 降级机制

当 OpenClaw gateway 的 subagent runtime 不可用时，subagent 节点会自动降级为 embedded-agent 执行，确保流程不中断。

### 审批模式

approval 类型节点在 subagent 执行时，系统会从 Agent 输出中提取 \`{approved: boolean}\` JSON 作为审批结果。`,

    "cli-script": `## 特有功能

### 参数传递

\`args\` 支持两种格式：

**数组格式**（作为位置参数）：
\`\`\`yaml
args:
  - "{{input.params.activityId}}"
  - "--verbose"
\`\`\`

**对象格式**（转为 --key value + ARG_KEY 环境变量）：
\`\`\`yaml
args:
  activity-id: "{{input.params.activityId}}"
  step: preprocess
\`\`\`

对象格式会同时生成 \`--activity-id value\` 命令行参数和 \`ARG_ACTIVITY_ID=value\` 环境变量。

### 用户身份注入

自动将当前用户身份注入为环境变量：
- \`CLAWFLOW_USER_ID\` - 用户 ID
- \`CLAWFLOW_USER_NAME\` - 用户名称
- \`CLAWFLOW_USER_CHANNEL\` - 消息通道
- \`CLAWFLOW_USER_CHAT_TYPE\` - 聊天类型
- \`CLAWFLOW_USER_IS_OWNER\` - 是否拥有者

### 路径展开

自动展开 \`~/\` 和 \`$HOME/\` 为实际 Home 目录路径。

### JSON 输出模式

当 \`outputMode: json\` 时，自动解析 stdout 为 JSON 对象，并检查 \`{success: false}\` / \`{status: "FAILED"}\` 错误指示。`,

    "mcp-call": `## 特有功能

### 确定性工具调用

与 embedded-agent 中的 LLM 驱动工具调用不同，mcp-call 是**确定性**的：
- 直接调用指定 MCP Server 上的指定工具
- 不经过 LLM 推理，参数固定
- 适合不需要智能判断的确定性 API 调用

### 调用方式

内部通过 \`mcporter call\` 命令行工具执行：
\`\`\`bash
mcporter call --server <server> --tool <tool> --args '<json>'
\`\`\`

### 输出模式

- \`text\` 模式：返回 \`{raw: stdout}\`
- 默认模式（或 \`json\`）：尝试解析 stdout 为 JSON，解析失败则返回 \`{raw: stdout}\``,

    "baas-call": `## 特有功能

### 两种调用模式

| 模式 | API 路径 | 说明 |
|------|----------|------|
| \`run\`（默认） | \`/openapi/v1/runs\` | 标准 BaaS 运行模式 |
| \`message\` | \`/openapi/v1/messages\` | 消息模式，需要 botId |

### 认证方式

- 默认使用 Bearer Token 认证
- 办公网络可通过 \`iamToken\` 配置 IAM Cookie 认证
- 自动检测认证失败（USER_NOT_LOGIN / TOKEN_INVALID / TOKEN_EXPIRED）

### 轮询机制

- 使用指数退避轮询（初始 \`pollIntervalMs\`，最大 15 秒）
- 自动检测 PENDING → RUNNING → COMPLETED/FAILED 状态
- 超时后节点失败

### 重试机制

提交请求时自带 3 次重试（仅 5xx 错误触发），每次间隔指数增长。`,

    approval: `## 特有功能

### 投递通道（delivery）

审批节点支持多种投递通道，按执行模式分别配置：

\`\`\`yaml
delivery:
  private:                          # 私聊模式
    primary: card-web               # card-dingtalk / card-web / card-secoc / subagent / embedded-agent
  dingtalkGroup:                    # 钉钉群聊模式
    primary: card-dingtalk
  collaboration:                    # BCS 协作模式
    primary: bcs-route              # bcs-route / subagent
\`\`\`

### 审批策略（approvalPolicy）

| 策略 | 说明 |
|------|------|
| \`any\` | 任一审批人通过即通过（默认） |
| \`all\` | 所有审批人必须通过 |
| \`majority\` | 多数通过即通过 |

### 跳过条件（skipWhen）

当模板变量值匹配时自动通过，不发送审批卡片：

\`\`\`yaml
skipWhen:
  nodeOutput.check-completeness.is_complete: true
\`\`\`

特别适用于 loop-group 内的审批节点，当前置节点已满足退出条件时跳过审批。

### 持久化（saveAs）

将审批回调结果写入 workflowData：

\`\`\`yaml
saveAs:
  workflowData.approvalResult: "approved"
  workflowData.approverNote: "note"
\`\`\`

### Web 审批页（card-web）

card-web 通道通过钉钉侧边栏打开 clawweb 审批页，无需登录。流程：
1. 写入 approval_cards DB 记录
2. 发送钉钉消息含侧边栏链接（含 cardId + empId）
3. 用户在 clawweb 页面审批
4. ClawMind 轮询 DB 检测状态变化`,

    human: `## 特有功能

### 三种动作

| 动作 | 说明 | 节点结果 |
|------|------|----------|
| confirm | 确认通过 | 节点 succeeded |
| revise | 修改后重新执行 | 目标节点重新执行 |
| reject | 拒绝 | 节点失败 |

### 输入表单（inputSchema）

定义用户需要填写的数据结构：

\`\`\`yaml
inputSchema:
  type: object
  required: [decision]
  properties:
    decision:
      type: string
      enum: [approve, reject, revise]
\`\`\`

### 动作配置（actions）

\`\`\`yaml
actions:
  confirm:
    inputSchema: { ... }
    saveAs:
      workflowData.confirmedBy: "{{input.approverName}}"
    next: succeed-current
  revise:
    feedbackPath: nodeOutput.collect-info.summary
    target: collect-info            # 重新执行的目标节点
    reset: target-and-descendants   # 同时重置目标及下游节点
    next: rerun-target
  reject:
    saveAs:
      workflowData.rejectReason: "{{input.reason}}"
    next: fail-flow
\`\`\`

### 命令提示（commandHints）

定义自然语言触发动作的关键词别名：

\`\`\`yaml
commandHints:
  confirm:
    label: "确认通过"
    args: ["通过", "同意", "ok"]
  revise:
    label: "修改"
    args: ["修改", "重做"]
  reject:
    label: "驳回"
    args: ["驳回", "拒绝"]
\`\`\`

### AI 解读模式

当不定义 \`keywordAliases\` / \`commandHints\` 时，用户的自然语言由 AI Agent 通过 \`workflow_choice\` 工具解读意图后触发分支。适用于选项难以枚举关键词的场景。`,

    "loop-group": `## 特有功能

### 循环机制

loop-group 节点按以下流程执行：
1. 初始化迭代变量 \`${'$'}{{iterationVar}}\` 为 0
2. 依次执行 body 中的节点列表
3. 检查 until 退出条件
4. 如果条件满足，退出循环；否则迭代变量 +1，重复步骤 2
5. 如果达到 maxIterations，执行 onMaxIterations 策略

### 退出条件（until）

\`\`\`yaml
until:
  node: validate-result       # 检查的节点 ID
  path: is_valid              # 节点输出中的字段路径
  equals: true                # 期望值
  orWorkflowData:             # 额外退出条件（OR 语义）
    workflowData.manualReject: "true"
\`\`\`

### 达到最大迭代次数

\`\`\`yaml
onMaxIterations:
  action: continue            # continue 使用最后一次结果 / fail 标记失败
  saveLastIteration: true     # 是否保存最后一次迭代结果
\`\`\`

### 循环体内节点

body 中的节点与普通工作流节点语法相同，只是它们在每次迭代中重新执行。循环体内可以使用 \`{{iterationVar}}\` 引用当前迭代索引。`,

    subworkflow: `## 特有功能

### 参数传递

子工作流接收 \`params\` 中定义的参数。如果未指定 params，则继承父工作流的所有参数：

\`\`\`yaml
params:
  activityId: "{{input.params.activityId}}"
  force: "true"
\`\`\`

### 输出映射

子工作流的 outputs 定义会被自动解析，将子工作流输出映射为节点结果。如果子工作流没有定义 outputs，则返回完整的 workflowData。

### 嵌套深度限制

最大嵌套深度为 **3 层**，超出则节点失败。这防止了无限递归。

### 失败策略

| 策略 | 说明 |
|------|------|
| \`fail\`（默认） | 子工作流失败时本节点失败 |
| \`retry\` | 配合 retry 配置重试子工作流 |
| \`skip\` | 子工作流失败时跳过，节点视为成功（结果为空） |
\`\`\`yaml
onFailure: retry     # 结合 retry 配置实现重试
retry:
  maxAttempts: 3
  backoffMs: 5000
\`\`\``,
  };

  return sections[nodeType] || "";
}

function getNodeNotes(nodeType) {
  const notes = {
    "embedded-agent": [
      "不支持 `contextPolicy.history: inherit` 时使用 `outputMode: json`（历史中可能包含非 JSON 输出）",
      "session 文件可能因异常路径（如 abandoned session）导致缓存失效，引擎会自动清理",
      "LLM 并发信号量的默认值为 3，可通过 `MAX_CONCURRENT_LLM_CALLS` 环境变量或 `llm.maxConcurrentCalls` 配置调整",
    ],
    subagent: [
      "不支持 `contextPolicy.history: inherit`（OpenClaw subagent runtime 当前不支持指定 sessionFile）",
      "不支持 `contextPolicy.history: tail`（subagent runtime 不提供 session file 访问）",
      "当 gateway subagent runtime 不可用时会自动降级为 embedded-agent",
    ],
    "cli-script": [
      "命令通过 `execFile` 执行，不支持管道 `|` 和重定向 `>`，如需请编写 Shell 脚本文件",
      "args 对象格式中 key 会被转换为 kebab-case 的 --参数 和 ARG_KEY 大写环境变量",
    ],
    "mcp-call": [
      "MCP Server 必须在 OpenClaw 配置中预先注册",
      "依赖 `mcporter` CLI 工具，需确保已安装",
    ],
    "baas-call": [
      "默认 baseUrl 为 `https://secbaas-prod.alipay.com`，测试环境需手动指定",
      "API Key 为系统对接固定值，不从环境变量读取（避免网关环境 key 失效）",
    ],
    approval: [
      "审批策略 `all` 和 `majority` 需要至少 2 个审批人才有意义",
      "skipWhen 条件为 AND 语义 — 所有条件都必须匹配才跳过",
      "card-web 通道依赖 clawweb 服务运行，需要配置 `clawwebUrl`",
    ],
    "bcs-route": [
      "需要在 BCS group session 上下文中运行（executionMode 为 bcs-group 且有 bcsGroupId）",
      "在非 BCS 环境中，路由信息会被记录但不实际发送，节点直接成功",
    ],
    human: [
      "人工节点的回复通过 OpenClaw 的用户消息拦截机制处理",
      "定义 commandHints 可启用 L1 关键词精确匹配；不定义则由 AI Agent 解读意图",
    ],
    "loop-group": [
      "body 中的节点在每次迭代中重新执行，状态独立",
      "until 条件中 `equals` 为 null 时表示检查字段为 null/undefined",
      "达到 maxIterations 后 onMaxIterations.action=continue 会使用最后一次迭代结果",
    ],
    subworkflow: [
      "最大嵌套深度 3 层（从顶层算起）",
      "子工作流的 businessStatus 初始为 INIT，继承父工作流的 executionMode 和 bcsGroupId",
    ],
  };
  return notes[nodeType] || [];
}

// ── 主流程 ────────────────────────────────────────────────────────────────

function main() {
  const args = process.argv.slice(2);
  const isDryRun = args.includes("--dry-run");
  const isCheck = args.includes("--check");
  const nodeFilter = args.find(a => a.startsWith("--node="))?.split("=")[1]
    || args.find((a, i) => args[i - 1] === "--node");

  console.log("══════════════════════════════════════════");
  console.log("  ClawMind 节点文档 → 语雀同步工具");
  console.log("══════════════════════════════════════════");
  console.log("");

  // ── Step 1: 解析 types.ts ──
  console.log("📖 解析 src/types.ts ...");
  const typesContent = fs.readFileSync(TYPES_FILE, "utf-8");
  const executorTypes = extractExecutorTypes(typesContent);

  // 建立 nodeType -> fields 映射
  const nodeTypeFields = {};
  for (const [typeName, fields] of Object.entries(executorTypes)) {
    const nodeType = executorTypeToNodeType(typeName);
    nodeTypeFields[nodeType] = fields;
  }
  console.log(`   找到 ${Object.keys(nodeTypeFields).length} 个执行器类型定义`);

  // ── Step 2: 提取默认值 ──
  console.log("📖 提取执行器默认值 ...");
  const allDefaults = {};
  for (const [nodeType, meta] of Object.entries(NODE_META)) {
    if (meta.executorFile) {
      const filePath = path.join(EXECUTORS_DIR, meta.executorFile);
      allDefaults[nodeType] = extractDefaultsFromExecutor(filePath);
    } else {
      allDefaults[nodeType] = {};
    }
  }

  // ── Step 3: 检查变化 ──
  const currentHash = computeHash();
  if (isCheck && fs.existsSync(CACHE_FILE)) {
    const cache = JSON.parse(fs.readFileSync(CACHE_FILE, "utf-8"));
    if (cache.hash === currentHash) {
      console.log("✅ 代码无变化，无需更新文档");
      return;
    }
    console.log("🔄 检测到代码变化，开始更新文档...");
  }

  // ── Step 4: 生成文档 ──
  const targetTypes = nodeFilter
    ? { [nodeFilter]: NODE_META[nodeFilter] }
    : NODE_META;

  const docs = {};
  for (const [nodeType, meta] of Object.entries(targetTypes)) {
    if (!NODE_META[nodeType]) {
      console.warn(`⚠️  未知节点类型: ${nodeType}`);
      continue;
    }

    console.log(`📝 生成文档: ${meta.displayName} (${nodeType})`);

    const fields = nodeTypeFields[nodeType] || [];
    const defaults = allDefaults[nodeType] || {};
    const yamlExamples = extractYamlExamples(PACKS_DIR, nodeType);

    docs[nodeType] = generateNodeDoc(nodeType, meta, fields, defaults, yamlExamples);
  }

  // ── Step 5: 生成总览文档 ──
  console.log("📝 生成总览文档 ...");
  docs["index"] = generateIndexDoc(docs);

  // ── Step 6: 输出/推送 ──
  if (isDryRun) {
    const outDir = path.join(PROJECT_ROOT, ".node-docs-output");
    if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
    for (const [nodeType, doc] of Object.entries(docs)) {
      const filePath = path.join(outDir, `${nodeType}.md`);
      fs.writeFileSync(filePath, doc, "utf-8");
      console.log(`   → ${filePath}`);
    }
    console.log(`\n✅ Dry-run 模式：文档已输出到 ${outDir}/`);
  } else {
    // 读取缓存获取已有 docIds
    const cache = fs.existsSync(CACHE_FILE)
      ? JSON.parse(fs.readFileSync(CACHE_FILE, "utf-8"))
      : {};
    const docIds = cache.docIds || {};

    // 输出推送指令
    const output = {
      bookId: YUQUE_BOOK_ID,
      hash: currentHash,
      generatedAt: new Date().toISOString(),
      actions: [],
    };

    for (const [nodeType, doc] of Object.entries(docs)) {
      const existingDocId = docIds[nodeType];
      if (existingDocId) {
        // 增量更新：已有文档，使用 skylark_doc_update
        output.actions.push({
          action: "update",
          nodeType,
          docId: existingDocId,
          title: nodeType === "index"
            ? "ClawMind 节点类型总览"
            : (NODE_META[nodeType]?.displayName || nodeType),
          body: doc,
        });
      } else {
        // 全量创建：新文档，使用 skylark_doc_create
        output.actions.push({
          action: "create",
          nodeType,
          title: nodeType === "index"
            ? "ClawMind 节点类型总览"
            : (NODE_META[nodeType]?.displayName || nodeType),
          body: doc,
        });
      }
    }

    const outputPath = path.join(PROJECT_ROOT, ".node-docs-output.json");
    fs.writeFileSync(outputPath, JSON.stringify(output, null, 2), "utf-8");

    // 同时输出 Markdown 文件供预览
    const outDir = path.join(PROJECT_ROOT, ".node-docs-output");
    if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
    for (const [nodeType, doc] of Object.entries(docs)) {
      fs.writeFileSync(path.join(outDir, `${nodeType}.md`), doc, "utf-8");
    }

    const updateCount = output.actions.filter(a => a.action === "update").length;
    const createCount = output.actions.filter(a => a.action === "create").length;
    console.log(`\n✅ 文档已生成到 ${outputPath}`);
    console.log(`   📊 ${createCount} 个新建 + ${updateCount} 个更新`);
    console.log("");
    console.log("   增量同步方式：");
    console.log("   - 已有文档（docId 在缓存中）→ 使用 skylark_doc_update(doc_id, body)");
    console.log("   - 新建文档（无 docId）     → 使用 skylark_doc_create(book_id, title, body)");
    console.log("");
    console.log("   使用 Claude Code 的语雀 MCP 工具推送，或运行：");
    console.log("   node scripts/sync-node-docs-to-yuque.mjs --push");
  }

  // ── Step 7: 更新缓存 ──
  const existingCache = fs.existsSync(CACHE_FILE)
    ? JSON.parse(fs.readFileSync(CACHE_FILE, "utf-8"))
    : {};
  fs.writeFileSync(CACHE_FILE, JSON.stringify({
    ...existingCache,
    hash: currentHash,
    updatedAt: new Date().toISOString(),
  }, null, 2), "utf-8");
}

function generateIndexDoc(docs) {
  const lines = [];
  lines.push("# ClawMind 节点类型总览");
  lines.push("");
  lines.push("> 本文档由 sync-node-docs-to-yuque.mjs 自动生成，描述 ClawMind 工作流引擎支持的所有节点执行器类型。");
  lines.push("");
  lines.push("## 节点类型一览");
  lines.push("");
  lines.push("| 节点类型 | 名称 | 分类 | 说明 |");
  lines.push("|----------|------|------|------|");

  for (const [nodeType, meta] of Object.entries(NODE_META)) {
    lines.push(`| \`${nodeType}\` | ${meta.displayName} | ${meta.category} | ${meta.description.split("。")[0]} |`);
  }

  lines.push("");
  lines.push("## 节点配置结构");
  lines.push("");
  lines.push("每个节点在 YAML 中遵循统一结构：");
  lines.push("");
  lines.push("```yaml");
  lines.push("- id: node-id              # 必填：节点唯一标识");
  lines.push("  title: 节点标题           # 必填：显示标题");
  lines.push("  phase: P1                # 必填：阶段标识");
  lines.push("  dependsOn: []            # 必填：前置依赖");
  lines.push("  executor:                # 必填：执行器配置");
  lines.push("    type: embedded-agent   # 执行器类型");
  lines.push("    # ... 各类型特有参数 ...");
  lines.push("  triggerRule: all_success # 可选：触发规则");
  lines.push("  retry:                   # 可选：重试配置");
  lines.push("    maxAttempts: 3");
  lines.push("    backoffMs: 3000");
  lines.push("  outputContract:          # 可选：输出契约");
  lines.push("    required: true");
  lines.push("    schema: { ... }");
  lines.push("  onResult:                # 可选：条件分支");
  lines.push("    if: { path: x, equals: y }");
  lines.push("    then: { complete: true }");
  lines.push("  progressMessage: 执行中  # 可选：进度提示");
  lines.push("```");
  lines.push("");
  lines.push("## 模板变量");
  lines.push("");
  lines.push("节点配置中支持 `{{var}}` 模板语法，在运行时动态替换：");
  lines.push("");
  lines.push("| 变量 | 说明 |");
  lines.push("|------|------|");
  lines.push("| `{{input.params.xxx}}` | 工作流输入参数 |");
  lines.push("| `{{input.message}}` | 用户发送的原始消息 |");
  lines.push("| `{{nodeOutput.nodeId}}` | 上游节点输出（整个 JSON 对象） |");
  lines.push("| `{{nodeOutput.nodeId.field}}` | 上游节点输出的特定字段 |");
  lines.push("| `{{workflowData.key}}` | 工作流共享数据 |");
  lines.push("| `{{input.params.xxx \\| default: val}}` | 带默认值的参数 |");
  lines.push("| `{{__user__.senderId}}` | 当前用户 ID |");
  lines.push("| `{{__user__.senderName}}` | 当前用户名 |");
  lines.push("");

  lines.push("---");
  lines.push("");
  lines.push(`*最后更新：${new Date().toISOString().split("T")[0]} | 由 sync-node-docs-to-yuque.mjs 自动生成*`);

  return lines.join("\n");
}

function computeHash() {
  try {
    // 计算 types.ts + executors/ 的 git hash
    const result = execSync(
      `git hash-object ${TYPES_FILE} 2>/dev/null; ` +
      `find ${EXECUTORS_DIR} -name "*.ts" -exec git hash-object {} \\; 2>/dev/null | sort`,
      { encoding: "utf-8", cwd: PROJECT_ROOT }
    ).trim();
    // 简单 hash：拼接后取前 16 字符
    const combined = result.replace(/\s+/g, "");
    let hash = 0;
    for (let i = 0; i < combined.length; i++) {
      hash = ((hash << 5) - hash + combined.charCodeAt(i)) | 0;
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
  } catch {
    return Date.now().toString(16);
  }
}

// ── 入口 ──────────────────────────────────────────────────────────────────

main();