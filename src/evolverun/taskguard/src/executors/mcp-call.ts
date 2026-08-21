import type { WorkflowNode, ExecutorResult, McpCallExecutor } from "../types.js";
import type { TemplateContext } from "../runner.js";
import { resolveTemplate, tryResolveSingleTemplate } from "../runner.js";
import { runOpenClawCommand, type CommandRunner } from "../command-runner.js";
import { jsonFailureError } from "./json-failure.js";

/**
 * Execute an mcp-call node: deterministically invoke an MCP tool with
 * resolved arguments via `mcporter call`, parse the JSON response.
 */
export async function executeMcpCall(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  runCommand: CommandRunner = runOpenClawCommand,
): Promise<ExecutorResult> {
  if (node.executor.type !== "mcp-call") {
    return { status: "failed", error: "not an mcp-call node" };
  }

  const executor = node.executor as McpCallExecutor;
  const timeoutMs = executor.timeoutMs ?? 30_000;

  const resolvedArgs: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(executor.args)) {
    if (typeof val === "string") {
      // 单模板 arg 透传原生类型(number/boolean/object),根治 MCP inputSchema 拒 string:
      // 此前 resolveTemplate 把 {{nodeOutput.x.docId}}(number)拍成 "123",对端要 number 直接拒。
      // 非单模板(拼接/多 token)或值缺失 → 仍走 resolveTemplate 字符串路径,行为不变。
      const single = tryResolveSingleTemplate(val, templateCtx);
      resolvedArgs[key] = single && single.value !== undefined
        ? single.value
        : resolveTemplate(val, templateCtx);
    } else {
      // args 类型标注为 string,但 YAML 偶有非字符串字面量;原值透传(JSON.stringify 能正确序列化)。
      resolvedArgs[key] = val;
    }
  }

  const argsJson = JSON.stringify(resolvedArgs, null, 0);
  const cmdParts = [
    "mcporter", "call",
    "--server", executor.server,
    "--tool", executor.tool,
    "--args", argsJson,
  ];

  try {
    const { stdout, stderr, code } = await runCommand({
      argv: cmdParts,
      timeoutMs,
      env: process.env as Record<string, string>,
    });

    if (code !== 0) {
      return {
        status: "failed",
        error: `MCP call exited with code ${code}. stderr: ${stderr.substring(0, 1000)}`,
      };
    }

    if (executor.outputMode === "text" || !stdout.trim().startsWith("{")) {
      return { status: "succeeded", result: { raw: stdout } };
    }

    try {
      const parsed = JSON.parse(stdout.trim());
      const failError = jsonFailureError(parsed);
      if (failError) {
        return { status: "failed", result: parsed, error: failError };
      }
      return { status: "succeeded", result: parsed };
    } catch {
      return {
        status: "failed",
        error: `MCP response is not valid JSON. stdout: ${stdout.substring(0, 500)}`,
      };
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { status: "failed", error: `MCP call failed: ${message}`, rawError: err };
  }
}