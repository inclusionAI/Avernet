/**
 * Guardian Agent — analyzes node failures at retry time and generates repair strategies.
 *
 * Uses the engine's own `executeEmbeddedAgent()` to perform the analysis,
 * so no external LLM API configuration is needed. The guardian constructs
 * a temporary embedded-agent analysis node, calls the engine's Agent runtime,
 * and parses the structured JSON response into a `GuardianRepair`.
 */
import { executeEmbeddedAgent } from "../executors/embedded-agent.js";
import type { EmbeddedAgentApi } from "../executors/embedded-agent.js";
import type { ExecuteEmbeddedAgentOptions } from "../executors/embedded-agent.js";
import type { WorkflowSpec, WorkflowNode } from "../types.js";
import type { TemplateContext } from "../runner.js";
import type { GuardianRepair, GuardianAnalysisParams, GuardianConfig } from "./types.js";

/** Construct the analysis prompt for the guardian agent. */
function buildAnalysisPrompt(params: GuardianAnalysisParams): string {
  return `你是 ClawMind 工作流引擎的守护 Agent。一个工作流节点执行失败了，请分析失败原因并给出修复策略。

## 失败信息
- 节点 ID: ${params.nodeId}
- 执行器类型: ${params.executorType}
- 错误信息: ${params.error.slice(0, 500)}
${params.resolvedPrompt ? `- 当前 prompt:\n\`\`\`\n${params.resolvedPrompt.slice(0, 2000)}\n\`\`\`` : ""}
${params.inputJson ? `- 节点输入: ${params.inputJson.slice(0, 1000)}` : ""}
${params.outputJson ? `- 节点输出: ${params.outputJson.slice(0, 1000)}` : ""}
- 重试: 第 ${params.attempt} 次 / 共 ${params.maxAttempts} 次

## 请分析并返回 JSON（不输出 Markdown 代码围栏）
{
  "failureReason": "prompt-ambiguity|timeout|param-type-mismatch|tool-not-found|output-contract|other",
  "repairAction": "patch-prompt|adjust-params|adjust-timeout|skip-retry|retry-as-is",
  "patchedPrompt": "修补后的完整 prompt（仅 patch-prompt 时）",
  "paramOverrides": {"key": "value"}（仅 adjust-params 时）,
  "timeoutOverride": 120000（仅 adjust-timeout 时，毫秒）,
  "reasoning": "分析推理过程"
}

## 修复策略说明
- patch-prompt: 修补原 prompt 使其更明确（如加输出格式说明、JSON schema 示例）
- adjust-params: 调整 mcp-call/cli-script 的参数（如类型转换、默认值）
- adjust-timeout: 增加超时时间（毫秒）
- skip-retry: 错误不可恢复（如工具不存在），不值得重试
- retry-as-is: 不确定如何修复，原样重试

只输出 JSON，不要输出其他内容。`;
}

/** Parse the agent's response into a GuardianRepair. */
function parseRepairResponse(raw: unknown): GuardianRepair {
  const fallback: GuardianRepair = {
    failureReason: "other",
    action: "retry-as-is",
    reasoning: "Failed to parse guardian agent response",
  };

  if (!raw || typeof raw !== "object") return fallback;

  const obj = raw as Record<string, unknown>;
  const action = String(obj.repairAction ?? obj.action ?? "retry-as-is") as GuardianRepair["action"];
  const failureReason = String(obj.failureReason ?? "other") as GuardianRepair["failureReason"];

  return {
    failureReason,
    action,
    patchedPrompt: typeof obj.patchedPrompt === "string" ? obj.patchedPrompt : undefined,
    paramOverrides: obj.paramOverrides && typeof obj.paramOverrides === "object"
      ? obj.paramOverrides as Record<string, unknown>
      : undefined,
    timeoutOverride: typeof obj.timeoutOverride === "number" ? obj.timeoutOverride : undefined,
    reasoning: typeof obj.reasoning === "string" ? obj.reasoning : "No reasoning provided",
  };
}

export class GuardianAgent {
  private config: GuardianConfig;

  constructor(
    private api: EmbeddedAgentApi,
    private options: {
      sessionKey: string;
      toolCtx?: ExecuteEmbeddedAgentOptions["toolCtx"];
      workflow?: WorkflowSpec;
      abortSignal?: AbortSignal;
      botId?: string;
    },
    config: GuardianConfig,
  ) {
    this.config = config;
  }

  /**
   * Analyze a node failure and return a repair strategy.
   * Uses executeEmbeddedAgent to let the engine's own Agent do the analysis.
   */
  async analyze(params: GuardianAnalysisParams): Promise<GuardianRepair> {
    if (!this.config.enabled) {
      return { failureReason: "other", action: "retry-as-is", reasoning: "Guardian agent disabled" };
    }

    try {
      // Construct temporary analysis node
      const analysisNode: WorkflowNode = {
        id: `__guardian_${params.nodeId}_${params.attempt}`,
        title: "守护 Agent 分析",
        phase: "P-guardian",
        dependsOn: [],
        executor: {
          type: "embedded-agent",
          outputMode: "json",
          prompt: buildAnalysisPrompt(params),
          timeoutSeconds: this.config.analysisTimeoutSeconds,
        },
      };

      const templateCtx: TemplateContext = {
        skillRoot: "",
        nodeOutput: {},
      };

      const result = await executeEmbeddedAgent(
        analysisNode,
        templateCtx,
        this.api,
        {
          sessionKey: this.options.sessionKey,
          toolCtx: this.options.toolCtx,
          flowId: `__guardian_${params.nodeId}`,
          abortSignal: this.options.abortSignal,
          botId: this.options.botId,
        },
      );

      if (result.status !== "succeeded" || !result.result) {
        return {
          failureReason: "other",
          action: "retry-as-is",
          reasoning: `Guardian agent analysis failed: ${result.error ?? "no result"}`,
        };
      }

      // result.result is the parsed JSON (outputMode: "json")
      return parseRepairResponse(result.result);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return {
        failureReason: "other",
        action: "retry-as-is",
        reasoning: `Guardian agent error: ${msg}`,
      };
    }
  }
}