import os from "node:os";
import path from "node:path";
import { statSync } from "node:fs";
import type { WorkflowNode, ExecutorResult, CliScriptExecutor } from "../types.js";
import type { TemplateContext } from "../runner.js";
import { resolveTemplate } from "../runner.js";
import { runOpenClawCommand, type CommandRunner } from "../command-runner.js";
import { jsonFailureError } from "./json-failure.js";

/**
 * Expand $HOME and ~ in a path string.
 * Without shell expansion, `$HOME/...` and `~/...` remain literal
 * when passed to child_process.execFile directly.
 */
function expandHome(str: string): string {
  if (str.startsWith("~/")) return path.join(os.homedir(), str.slice(2));
  if (str.startsWith("$HOME/")) return path.join(os.homedir(), str.slice(6));
  if (str.startsWith("${HOME}/")) return path.join(os.homedir(), str.slice(7));
  return str;
}

/**
 * True only when `p` is an existing directory. Node's spawn runs chdir(cwd)
 * before resolving the binary, so a non-existent cwd makes chdir fail and
 * surface as a misleading `spawn <bin> ENOENT`. DB/API-deployed workflows get
 * an empty pack.root and the workspace-symlink fallback may not exist, so the
 * cwd we're handed must be validated — falling back to the declared packRoot,
 * or to none (absolute-path scripts then run from the process cwd).
 */
function isRealDir(p: string | undefined): p is string {
  if (!p) return false;
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
}

export async function executeCliScript(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  runCommand: CommandRunner = runOpenClawCommand,
  cwd?: string,
): Promise<ExecutorResult> {
  if (node.executor.type !== "cli-script") {
    return { status: "failed", error: "not a cli-script node" };
  }

  const executor = node.executor as CliScriptExecutor;
  const resolvedCommand = expandHome(resolveTemplate(executor.command, templateCtx));
  const timeoutMs = executor.timeoutMs ?? (executor.timeoutSeconds ? executor.timeoutSeconds * 1000 : 120_000);

  const env: Record<string, string> = {};
  const cliArgs: string[] = [];

  // Inject CLAWFLOW_USER_* env vars from __user__ template context
  const userIdentity = templateCtx.__user__ as Record<string, unknown> | undefined;
  if (userIdentity) {
    env.CLAWFLOW_USER_ID = String(userIdentity.senderId ?? "");
    if (userIdentity.senderName) env.CLAWFLOW_USER_NAME = String(userIdentity.senderName);
    env.CLAWFLOW_USER_CHANNEL = String(userIdentity.channel ?? "unknown");
    env.CLAWFLOW_USER_CHAT_TYPE = String(userIdentity.chatType ?? "unknown");
    env.CLAWFLOW_USER_IS_OWNER = String(userIdentity.isOwner ?? true);
  }

  if (executor.args) {
    if (Array.isArray(executor.args)) {
      for (const val of executor.args) {
        const resolvedVal = expandHome(resolveTemplate(val, templateCtx));
        cliArgs.push(resolvedVal);
      }
    } else {
      for (const [key, val] of Object.entries(executor.args)) {
        const resolvedVal = expandHome(resolveTemplate(val, templateCtx));
        env[`ARG_${key.toUpperCase()}`] = resolvedVal;
        cliArgs.push(`--${key}`, resolvedVal);
      }
    }
  }

  if (executor.env) {
    for (const [key, val] of Object.entries(executor.env)) {
      const resolvedVal = resolveTemplate(val, templateCtx);
      env[key] = resolvedVal;
    }
  }

  const parts = resolvedCommand.split(/\s+/);
  const bin = parts[0];
  const scriptArgs = [...parts.slice(1), ...cliArgs];

  const declaredPackRoot = typeof templateCtx.packRoot === "string" ? templateCtx.packRoot : undefined;
  const effectiveCwd = isRealDir(cwd)
    ? cwd
    : (isRealDir(declaredPackRoot) ? declaredPackRoot : undefined);

  try {
    const { stdout, stderr, code } = await runCommand({
      argv: [bin, ...scriptArgs],
      timeoutMs,
      cwd: effectiveCwd,
      env: { ...process.env, ...env },
    });

    if (code !== 0) {
      return {
        status: "failed",
        error: `Script exited with code ${code}. stderr: ${stderr.substring(0, 1000)}`,
      };
    }

    if (executor.outputMode === "json") {
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
          error: `Script stdout is not valid JSON. stdout: ${stdout.substring(0, 500)}`,
        };
      }
    }

    return { status: "succeeded", result: { stdout, stderr } };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { status: "failed", error: `Script execution failed: ${message}`, rawError: err };
  }
}