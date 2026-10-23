import { posix as posixPath } from "node:path";
import { randomUUID } from "node:crypto";
import type { AgentPlanAction, AgentRuntimeInspectInput, AgentToolContext } from "./contracts.js";
import {
  BotEngineOperations,
  BotRuntimeError,
  type BotRuntime,
  type BotSessionTrajectoryReader,
} from "@avernet/clawweb-shared/server/services/bot-runtime";
import { AgentToolError, agentToolValidation } from "./errors.js";
import { redactPersistableLines } from "@avernet/clawweb-shared/server/services/redaction";
import { redactEvidenceValue } from "@avernet/clawweb-shared/server/services/redaction";
const redactEngineResult = (value: Record<string, unknown>) => redactEvidenceValue(value) as Record<string, unknown>;
import { evidenceLocatorsFromText } from "./evidence-locators.js";


function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function integer(value: unknown, fallback: number, min: number, max: number, field: string): number {
  const normalized = value == null ? fallback : Number(value);
  if (!Number.isSafeInteger(normalized) || normalized < min || normalized > max) {
    agentToolValidation("invalid_runtime_argument", `${field} 必须在 ${min} 到 ${max} 之间`);
  }
  return normalized;
}

function safePath(value: unknown): string {
  const path = typeof value === "string" ? value.trim() : "";
  if (!path.startsWith("/") || path.length > 1_024 || /[\r\n\0]/u.test(path)) {
    agentToolValidation("invalid_runtime_path", "path 必须是合法绝对路径");
  }
  return path.replaceAll(/\/{2,}/g, "/");
}

function resolvedPathPrelude(path: string): string {
  return `p=$(realpath -e -- ${shellQuote(path)}) || exit 44;`;
}

function searchablePathGuard(): string {
  return `case "$p" in /proc|/proc/*|/sys|/sys/*|/dev|/dev/*) exit 45 ;; esac;`;
}

function readableFileGuard(): string {
  return `case "$p" in /proc/kcore|/proc/*/mem) exit 45 ;; esac; test -f "$p" || exit 47;`;
}

function boundedText(value: unknown, field: string, maxLength: number): string {
  const text = typeof value === "string" ? value : "";
  if (!text || text.length > maxLength || /[\r\n\0]/u.test(text)) {
    agentToolValidation("invalid_runtime_argument", `${field} 格式不合法`);
  }
  return text;
}

function assertAdapterToolAccess(command: string): void {
  const normalized = command.replace(/\\\r?\n/g, "").replace(/['"]/g, "");
  if (/\bopenclaw\s+(?:cron|gateway|config|skills|mcp|plugins)\b|\bmcporter\s+config\s+(?:add|remove|delete|set|import)\b|\/api\/(?:mcp|skills|cron|engine|file)(?:[\/\s?'"]|$)|\/api\/v1\/(?:bot\/(?:apply|cron)|file\/(?:read|list|upload|remove|skill-package))(?:[\/\s?'"]|$)/i.test(normalized)) {
    agentToolValidation("adapter_tool_required", "引擎操作必须使用已声明的接口工具；MCP 和用户 Skill 变更交给 Owner，不能用 Shell、CLI 或直接 HTTP 绕过");
  }
}

/** Reject explicit management bypasses, not a general-purpose Shell parser. */
export function assertAgentContainerCommand(command: string): void {
  assertAdapterToolAccess(command);
  if (BotEngineOperations.isManagedPath(command)) agentToolValidation("managed_runtime_file", "Shell 动作不能变更 MCP、用户 Skill 或 Cron 管理文件；读取请使用调查工具");
}

export function buildAgentRuntimeCommand(request: AgentRuntimeInspectInput, frameMarker = "REPAIR_EVIDENCE_PREVIEW", options: { allowSessionHistory?: boolean } = {}): string {
  if (!/^[A-Za-z0-9_-]+$/.test(frameMarker)) agentToolValidation("invalid_runtime_frame", "读取回执标识无效");
  const unsafe = request as AgentRuntimeInspectInput & {
    command?: unknown; cmd?: unknown; url?: unknown; method?: unknown;
  };
  if ((request.operation !== "shell_exec" && unsafe.command != null)
    || unsafe.cmd != null || unsafe.url != null || unsafe.method != null) {
    agentToolValidation("raw_runtime_command_forbidden", "Repair 只读工具不接受原始 command、URL 或 HTTP method");
  }

  const historyGuard = options.allowSessionHistory === false
    ? 'case "$p" in */.openclaw/agents/*/sessions|*/.openclaw/agents/*/sessions/*|*/.claude/projects|*/.claude/projects/*) exit 45 ;; esac;' : '';
  const historyPrune = options.allowSessionHistory === false
    ? "\\( -path '*/.openclaw/agents/*/sessions' -o -path '*/.claude/projects' \\) -prune -o " : '';
  const prelude = (path: string) => `${resolvedPathPrelude(path)} ${historyGuard}`;
  switch (request.operation) {
    case "fs_list": {
      const path = safePath(request.path);
      const maxEntries = integer(request.maxEntries, 200, 1, 500, "maxEntries");
      return `${prelude(path)} test -d "$p" || exit 46; find -P "$p" -mindepth 1 -maxdepth 1 -printf '%y\\t%p\\n' | head -n ${maxEntries}`;
    }
    case "fs_find": {
      const path = safePath(request.path);
      const name = boundedText(request.name, "name", 256);
      const maxDepth = integer(request.maxDepth, 4, 1, 12, "maxDepth");
      const maxEntries = integer(request.maxEntries, 200, 1, 500, "maxEntries");
      return `${prelude(path)} ${searchablePathGuard()} test -d "$p" || exit 46; find -P "$p" -maxdepth ${maxDepth} -name ${shellQuote(name)} -printf '%y\\t%p\\n' | head -n ${maxEntries}`;
    }
    case "fs_stat": {
      const path = safePath(request.path);
      return `${prelude(path)} stat --printf='%F\\t%a\\t%U:%G\\t%s\\t%y\\t%n\\n' -- "$p"`;
    }
    case "fs_read": {
      const path = safePath(request.path);
      const startLine = integer(request.startLine, 1, 1, 1_000_000, "startLine");
      const lines = integer(request.lines, 200, 1, 500, "lines");
      const endLine = startLine + lines - 1;
      // One extra line distinguishes a bounded slice from the actual EOF.
      const program = `NR >= ${startLine} && NR <= ${endLine} { print; count++ }
NR == ${endLine + 1} { more=1; exit }
END { printf "\\n${frameMarker}\\t{\\"kind\\":\\"fs_read\\",\\"count\\":%d,\\"hasMore\\":%s}\\n", count, more ? "true" : "false" }`;
      const reader = `awk ${shellQuote(program)}`;
      return `${prelude(path)} ${readableFileGuard()} case "$p" in /proc/[0-9]*/environ|/proc/[0-9]*/cmdline) tr '\\000' '\\n' < "$p" | ${reader} ;; *) ${reader} "$p" ;; esac`;
    }
    case "fs_search": {
      const path = safePath(request.path);
      const pattern = boundedText(request.pattern, "pattern", 512);
      const maxMatches = integer(request.maxMatches, 200, 1, 500, "maxMatches");
      const mode = searchMatchMode(request.matchMode), flag = mode === "regex" ? "E" : "F";
      const validatePattern = mode === "regex"
        ? `printf '' | grep -E -- ${shellQuote(pattern)} >/dev/null; search_status=$?; if test "$search_status" -gt 1; then exit "$search_status"; fi; ` : "";
      const program = `NR <= ${maxMatches} { print; count++ }
NR == ${maxMatches + 1} { more=1; exit 10 }
END { printf "\\n${frameMarker}\\t{\\"kind\\":\\"fs_search\\",\\"count\\":%d,\\"hasMore\\":%s}\\n", count, more ? "true" : "false" }`;
      // Normalize expected early pipe closure only when the reader observed overflow.
      // A producer error without overflow must not become a successful empty search.
      return `${prelude(path)} ${searchablePathGuard()} ${validatePattern}{ if test -d "$p"; then find -P "$p" ${historyPrune}-type f -exec grep -nH${flag} --binary-files=without-match -- ${shellQuote(pattern)} {} +; else ${readableFileGuard()} grep -n${flag} --binary-files=without-match -- ${shellQuote(pattern)} "$p"; fi; } | awk ${shellQuote(program)}; search_codes=("\${PIPESTATUS[@]}"); if test "\${search_codes[1]}" = 10; then exit 0; fi; if test "\${search_codes[1]}" != 0; then exit "\${search_codes[1]}"; fi; if test "\${search_codes[0]}" -gt 1; then exit "\${search_codes[0]}"; fi`;
    }
    case "process_list": {
      if (request.pattern == null || request.pattern === "") return "ps -ef";
      const pattern = boundedText(request.pattern, "pattern", 256);
      return `ps -ef | grep -F -- ${shellQuote(pattern)} | grep -v '[g]rep -F' | head -n 200`;
    }
    case "port_list":
      return "ss -lntp";
    case "http_get": {
      const port = integer(request.port, 0, 1, 65_535, "port");
      const path = boundedText(request.path, "path", 2_048);
      if (!path.startsWith("/") || path.startsWith("//") || path.includes("://")) {
        agentToolValidation("invalid_loopback_path", "http_get 只允许 loopback 绝对 URL path");
      }
      assertAdapterToolAccess(path);
      return `curl -fsS --max-time 10 -- ${shellQuote(`http://127.0.0.1:${port}${path}`)}`;
    }
    case "shell_exec": {
      if (typeof request.command !== "string" || !request.command.trim()
        || request.command.length > 16_384 || request.command.includes("\0")) {
        agentToolValidation("invalid_runtime_command", "command 必须是 1..16384 字符且不含 NUL 的 shell 命令");
      }
      assertAdapterToolAccess(request.command);
      const encoded = Buffer.from(request.command, "utf8").toString("base64");
      return `cd /home/admin 2>/dev/null || cd /; printf %s ${shellQuote(encoded)} | base64 -d | bash --noprofile --norc`;
    }
    default:
      return agentToolValidation("unsupported_runtime_operation", "不支持的 Repair 只读操作");
  }
}

function searchMatchMode(value: unknown): "literal" | "regex" {
  if (value == null) return "literal";
  if (value !== "literal" && value !== "regex") agentToolValidation("invalid_runtime_argument", "matchMode 必须是 literal 或 regex（POSIX ERE）");
  return value;
}

type FileEvidenceFrame = { kind: string; count: number; hasMore: boolean };
function decodeFileEvidence(raw: string, marker: string, operation: string): { stdout: string; frame: FileEvidenceFrame | null } {
  const position = raw.lastIndexOf(`\n${marker}\t`);
  if (position < 0) return { stdout: raw, frame: null };
  try {
    const frame = JSON.parse(raw.slice(position + marker.length + 2)) as FileEvidenceFrame;
    if (frame.kind !== operation || !Number.isSafeInteger(frame.count) || frame.count < 0 || frame.count > 500 || typeof frame.hasMore !== "boolean") return { stdout: raw, frame: null };
    return { stdout: raw.slice(0, position), frame };
  } catch { return { stdout: raw, frame: null }; }
}
function fileCoverage(request: AgentRuntimeInspectInput, frame: FileEvidenceFrame | null,
  raw: string, stdout: string, succeeded: boolean, stderr: string): Record<string, unknown> {
  if (!["fs_read", "fs_search"].includes(request.operation)) return {};
  const outputTruncated = raw.includes("[TRUNCATED]") || stdout.includes("[TRUNCATED]");
  const verified = !!frame && succeeded && !stderr.trim();
  const hasMore = verified ? frame!.hasMore : null;
  if (request.operation === "fs_read") {
    const startLine = request.startLine ?? 1, requestedLines = request.lines ?? 200;
    const count = frame?.count ?? null, endLine = count ? startLine + count - 1 : null;
    return { readCoverage: { startLine, endLine: outputTruncated ? null : endLine, selectedEndLine: endLine, requestedLines, selectedLines: count, metadataVerified: verified,
      eof: hasMore === null ? null : !hasMore, hasMore, outputTruncated,
      completeFile: verified && !outputTruncated && startLine === 1 && !hasMore,
      nextStartLine: verified && !outputTruncated && hasMore && endLine !== null ? endLine + 1 : null,
      explanation: "范围是远端程序选取的行；输出被裁剪时不能视为这些行已完整返回。局部索引不能排除其他会话或证明会话结束；EOF 只证明本次读取到文件尾。" } };
  }
  if (request.operation === "fs_search") {
    const maxMatches = request.maxMatches ?? 200, matchMode = searchMatchMode(request.matchMode);
    const matchingLinesBeforeTimeFilter = frame?.count ?? stdout.split(/\r?\n/u).filter(line => line && line !== "[TRUNCATED]").length;
    const complete = verified && !outputTruncated && !hasMore;
    return { searchCoverage: { maxMatches, matchMode,
      patternSyntax: matchMode === "regex" ? "POSIX ERE" : "fixed string; metacharacters are literal",
      matchingLinesBeforeTimeFilter, limitReached: matchingLinesBeforeTimeFilter >= maxMatches,
      hasMore, outputTruncated, metadataVerified: verified, complete, remainingMatches: hasMore ? "present" : complete ? "none" : "unknown",
      explanation: "完整性仅针对指定路径和匹配条件。上限、裁剪、读取错误或缺少覆盖回执时，剩余匹配未知；未返回错误不能证明其他路径或原故障不存在。" } };
  }
  return {};
}

function safeOutput(value: unknown): string {
  return redactPersistableLines(value, 32 * 1024);
}

const EVIDENCE_LOCATOR_RUNTIME_OPERATIONS = new Set([
  "fs_list", "fs_find", "fs_stat", "fs_read", "fs_search", "process_list",
]);
const STRUCTURED_FILESYSTEM_OPERATIONS = new Set([
  "fs_list", "fs_find", "fs_stat", "fs_read", "fs_search",
]);

function verifiedOutputLocators(
  operation: string,
  stdout: string,
  stderr: string,
  requestLocators: readonly string[],
): string[] {
  if (!EVIDENCE_LOCATOR_RUNTIME_OPERATIONS.has(operation)) return [];
  if (STRUCTURED_FILESYSTEM_OPERATIONS.has(operation)) {
    if (operation !== "fs_list" || requestLocators.length !== 1) return [...new Set(requestLocators)];
    const requestedRoot = posixPath.normalize(requestLocators[0]).replace(/\/+$/u, "") || "/";
    const listedChildren = stdout.split(/\r?\n/u).flatMap((line) => {
      const match = /^([fd])\t(\/[^\t\r\n]+)$/u.exec(line);
      if (!match) return [];
      const rawCandidate = match[2];
      if (/[\u0000-\u001f\u007f]/u.test(rawCandidate)
        || rawCandidate.split("/").some((segment) => segment === "." || segment === "..")) {
        return [];
      }
      const candidate = posixPath.normalize(rawCandidate);
      return posixPath.dirname(candidate) === requestedRoot ? [candidate] : [];
    });
    return [...new Set([...requestLocators, ...listedChildren])];
  }
  const echoed = new Set(requestLocators);
  return evidenceLocatorsFromText(stdout, stderr).filter((locator) => !echoed.has(locator));
}

function shellObservedLocators(operation: string, stdout: string, stderr: string): string[] {
  return operation === "shell_exec" ? evidenceLocatorsFromText(stdout, stderr) : [];
}

export class AgentRuntimeTool {
  constructor(private readonly runtime: Pick<BotRuntime, "executeShell"> & Partial<Pick<BotRuntime, "request">>, private readonly trajectory?: BotSessionTrajectoryReader) {}
  get hasTrajectories() { return !!this.trajectory; }

  async inspect(
    context: AgentToolContext,
    request: AgentRuntimeInspectInput,
    options: { allowSessionHistory?: boolean } = {},
  ): Promise<Record<string, unknown>> {
    if (request.operation === "trajectory_read") {
      if (!this.trajectory) throw new AgentToolError(503, "trajectory_unavailable", "轨迹读取未配置");
      const offset = request.offset ?? 0, limit = request.limit ?? 20;
      if (typeof request.sessionKey !== "string" || !request.sessionKey.trim() || request.sessionKey.length > 1024 || !Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) agentToolValidation("invalid_trajectory_range", "轨迹范围无效");
      return redactEngineResult(await this.trajectory.read({ target: context.target, sessionKey: request.sessionKey, offset, limit, traceId: request.traceId }));
    }
    if (request.operation === "engine_read") return this.engine(context, request.request, false);
    const requestFields = request as AgentRuntimeInspectInput & { path?: unknown };
    const requestLocators = STRUCTURED_FILESYSTEM_OPERATIONS.has(request.operation)
      ? evidenceLocatorsFromText(typeof requestFields.path === "string" ? requestFields.path : "")
      : [];
    const frameMarker = `REPAIR_EVIDENCE_${randomUUID()}`;
    return this.execute(
      context,
      request.operation,
      buildAgentRuntimeCommand(request, frameMarker, options),
      requestLocators,
      { request, frameMarker },
    );
  }

  async applyApprovedAction(
    context: AgentToolContext,
    action: AgentPlanAction,
  ): Promise<Record<string, unknown>> {
    if (action.type === "engine_operation" && action.engineOperation) return this.engine(context, action.engineOperation, true);
    if (action.type !== "container_command" || !action.command) {
      agentToolValidation("unsupported_repair_action", "当前只支持执行获批的 container_command");
    }
    if (action.command.length > 16_384 || action.command.includes("\0")) {
      agentToolValidation("invalid_approved_command", "获批 command 格式不合法");
    }
    assertAgentContainerCommand(action.command);
    const encoded = Buffer.from(action.command, "utf8").toString("base64");
    return this.execute(
      context,
      `apply_action:${action.actionId}`,
      `printf %s ${shellQuote(encoded)} | base64 -d | bash`,
      evidenceLocatorsFromText(action.command),
    );
  }

  private async engine(context: AgentToolContext, request: import("@avernet/clawweb-shared/server/services/bot-runtime").EngineOperation, write: boolean) {
    if (!this.runtime.request) throw new AgentToolError(503, "engine_transport_unavailable", "Engine 接口未配置");
    return redactEngineResult(await new BotEngineOperations({ request: this.runtime.request.bind(this.runtime) }).execute(context.target, request, write));
  }

  private async execute(
    context: AgentToolContext,
    operation: string,
    command: string,
    requestLocators: readonly string[],
    fileEvidence?: { request: AgentRuntimeInspectInput; frameMarker: string },
  ): Promise<Record<string, unknown>> {
    const target = context.target;
    try {
      const result = await this.runtime.executeShell({ target, command, deviceAffinity: context.taskId });
      const decoded = fileEvidence ? decodeFileEvidence(result.stdout, fileEvidence.frameMarker, operation) : { stdout: result.stdout, frame: null };
      const stdout = safeOutput(decoded.stdout), stderr = safeOutput(result.stderr);
      const coverage = fileEvidence ? fileCoverage(fileEvidence.request, decoded.frame, decoded.stdout, stdout,
        result.status === "success" && result.exitCode === 0, String(result.stderr ?? "")) : {};

      return {
        status: result.status, operation,
        target: { environment: target.environment, bindingId: target.bindingId,
          ...(target.sandboxId ? { sandboxId: target.sandboxId } : { deviceId: target.deviceId }) },
        exitCode: result.exitCode, stdout, stderr, durationMs: result.durationMs, ...coverage,
        ...(result.error ? { error: result.error } : {}),
        evidenceLocators: result.status === "success" ? verifiedOutputLocators(operation, stdout, stderr, requestLocators) : [],
        shellObservedLocators: result.status === "success" ? shellObservedLocators(operation, stdout, stderr) : [],
      };
    } catch (error) {
      if (error instanceof BotRuntimeError) throw new AgentToolError(error.status, error.code, error.message);
      throw error;
    }
  }
}
