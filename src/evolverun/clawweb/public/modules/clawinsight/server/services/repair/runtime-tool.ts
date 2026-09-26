import { posix as posixPath } from "node:path";
import { buildRepairExecutionContextCommand, repairExecutionContextBinding } from "./execution-context.js";
import type { ResolvedBaasConfig } from "@avernet/clawweb-shared/server/db";
import type {
  RepairPlanAction,
  RepairRuntimeInspectInput,
  RepairTaskContext,
} from "./contracts.js";
import {
  executeBaasCommand,
  resolveBaasCommandTarget,
  type BaasCommandResponse,
  type BaasCommandTargetConfig,
} from "@avernet/clawevolve/server/services/baas-command-transport";
import { RepairError, repairUnavailable, repairValidation } from "./errors.js";
import { redactPersistableLines, redactText } from "./redaction.js";
import type { ArcaCommandTransport } from "./arca-command-transport.js";
import { evidenceLocatorsFromText } from "./evidence-locators.js";

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

export const REPAIR_RUNTIME_USER = "admin";
const REPAIR_RUNTIME_HOME = "/home/admin";
const REPAIR_RUNTIME_USER_EXIT_CODE = 78;

/**
 * Remote command transports inherit the container's default user. Legacy ARCA
 * sandboxes may therefore start commands as root even though OpenClaw runs as
 * admin. Drop that inherited privilege before every Repair read or approved
 * container action, and fail closed for any other execution identity.
 */
export function buildRepairRuntimeUserCommand(command: string): string {
  const payload = [
    `export HOME=${REPAIR_RUNTIME_HOME} USER=${REPAIR_RUNTIME_USER} LOGNAME=${REPAIR_RUNTIME_USER}`,
    `cd ${REPAIR_RUNTIME_HOME} || exit ${REPAIR_RUNTIME_USER_EXIT_CODE}`,
    "umask 077",
    `exec bash --noprofile --norc -c ${shellQuote(command)}`,
  ].join("; ");
  const identityError = "Repair target command must run as admin";
  return [
    `repair_uid=$(id -u) || exit ${REPAIR_RUNTIME_USER_EXIT_CODE}`,
    'if [ "$repair_uid" = "0" ]; then',
    `  id ${REPAIR_RUNTIME_USER} >/dev/null 2>&1 || { printf '%s\\n' ${shellQuote(identityError)} >&2; exit ${REPAIR_RUNTIME_USER_EXIT_CODE}; }`,
    `  command -v su >/dev/null 2>&1 || { printf '%s\\n' ${shellQuote(identityError)} >&2; exit ${REPAIR_RUNTIME_USER_EXIT_CODE}; }`,
    `  exec su ${REPAIR_RUNTIME_USER} -c ${shellQuote(payload)}`,
    "fi",
    `test "$(id -un)" = ${REPAIR_RUNTIME_USER} || { printf '%s\\n' ${shellQuote(identityError)} >&2; exit ${REPAIR_RUNTIME_USER_EXIT_CODE}; }`,
    `exec bash --noprofile --norc -c ${shellQuote(payload)}`,
  ].join("\n");
}

function integer(value: unknown, fallback: number, min: number, max: number, field: string): number {
  const normalized = value == null ? fallback : Number(value);
  if (!Number.isSafeInteger(normalized) || normalized < min || normalized > max) {
    repairValidation("invalid_runtime_argument", `${field} 必须在 ${min} 到 ${max} 之间`);
  }
  return normalized;
}

function searchMatchMode(value: unknown): "literal" | "regex" {
  if (value == null) return "literal"; // Preserve historical callers.
  if (value !== "literal" && value !== "regex") {
    repairValidation("invalid_runtime_argument", "matchMode 必须是 literal 或 regex（POSIX ERE）");
  }
  return value;
}

function safePath(value: unknown): string {
  const path = typeof value === "string" ? value.trim() : "";
  if (!path.startsWith("/") || path.length > 1_024 || /[\r\n\0]/u.test(path)) {
    repairValidation("invalid_runtime_path", "path 必须是合法绝对路径");
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
    repairValidation("invalid_runtime_argument", `${field} 格式不合法`);
  }
  return text;
}

const MANAGED_RUNTIME_CLI = /(?:^|[\n;&|()]|\$\()\s*(?:(?:[A-Za-z_][A-Za-z0-9_]*=[^\s;&|()]+)\s+)*(?:\/[A-Za-z0-9._+\/-]+\/)?(?:openclaw|cfuse)(?=$|[\s;&|()])/iu;

/**
 * A management CLI can start or reconnect the managed runtime even for a
 * command named list/show/status.  That makes later process and log output a
 * consequence of the Repair probe itself rather than an observation of the
 * pre-existing incident.  Plan investigation must use structured runtime
 * reads, loopback APIs whose contract was established from matching source,
 * or source inspection instead.
 */
export function assertRepairPlanShellIsObservational(
  context: RepairTaskContext,
  request: RepairRuntimeInspectInput,
): void {
  if (context.phase !== "repair_plan" || request.operation !== "shell_exec") return;
  const command = typeof request.command === "string" ? request.command : "";
  if (!MANAGED_RUNTIME_CLI.test(command)) return;
  repairValidation(
    "repair_plan_runtime_cli_forbidden",
    "Plan 阶段未执行该命令：直接调用目标运行时管理 CLI 即使是 list/show/status 也可能启动进程或改变日志现场；请改用结构化文件、进程、端口读取，或先从匹配版本源码确认只读 loopback API",
  );
}

export function buildRepairRuntimeCommand(request: RepairRuntimeInspectInput): string {
  const unsafe = request as RepairRuntimeInspectInput & {
    command?: unknown; cmd?: unknown; url?: unknown; method?: unknown;
  };
  if ((request.operation !== "shell_exec" && unsafe.command != null)
    || unsafe.cmd != null || unsafe.url != null || unsafe.method != null) {
    repairValidation("raw_runtime_command_forbidden", "Repair 只读工具不接受原始 command、URL 或 HTTP method");
  }

  if (request.operation === "execution_context"
    && Object.keys(request).some(key => !["operation", "clientRequestId", "purpose"].includes(key))) {
    repairValidation("invalid_runtime_argument", "execution_context 不接受额外参数");
  }
  switch (request.operation) {
    case "execution_context":
      return buildRepairExecutionContextCommand();
    case "fs_list": {
      const path = safePath(request.path);
      const maxEntries = integer(request.maxEntries, 200, 1, 500, "maxEntries");
      return `${resolvedPathPrelude(path)} test -d "$p" || exit 46; find -P "$p" -mindepth 1 -maxdepth 1 -printf '%y\\t%p\\n' | head -n ${maxEntries}`;
    }
    case "fs_find": {
      const path = safePath(request.path);
      const name = boundedText(request.name, "name", 256);
      const maxDepth = integer(request.maxDepth, 4, 1, 12, "maxDepth");
      const maxEntries = integer(request.maxEntries, 200, 1, 500, "maxEntries");
      return `${resolvedPathPrelude(path)} ${searchablePathGuard()} test -d "$p" || exit 46; find -P "$p" -maxdepth ${maxDepth} -name ${shellQuote(name)} -printf '%y\\t%p\\n' | head -n ${maxEntries}`;
    }
    case "fs_stat": {
      const path = safePath(request.path);
      return `${resolvedPathPrelude(path)} stat --printf='%F\\t%a\\t%U:%G\\t%s\\t%y\\t%n\\n' -- "$p"`;
    }
    case "fs_read": {
      const path = safePath(request.path);
      const startLine = integer(request.startLine, 1, 1, 1_000_000, "startLine");
      const lines = integer(request.lines, 200, 1, 500, "lines");
      const endLine = startLine + lines - 1;
      return `${resolvedPathPrelude(path)} ${readableFileGuard()} case "$p" in /proc/[0-9]*/environ|/proc/[0-9]*/cmdline) tr '\\000' '\\n' < "$p" | sed -n '${startLine},${endLine}p' ;; *) sed -n '${startLine},${endLine}p' -- "$p" ;; esac`;
    }
    case "fs_search": {
      const path = safePath(request.path);
      const pattern = boundedText(request.pattern, "pattern", 512);
      const maxMatches = integer(request.maxMatches, 200, 1, 500, "maxMatches");
      const mode = searchMatchMode(request.matchMode);
      const flag = mode === "regex" ? "E" : "F";
      // Validate ERE before the head pipeline: grep's syntax error must never
      // become a successful empty search merely because head exits zero.
      const validatePattern = mode === "regex"
        ? `printf '' | grep -E -- ${shellQuote(pattern)} >/dev/null; search_status=$?; if test "$search_status" -gt 1; then exit "$search_status"; fi; `
        : "";
      return `${resolvedPathPrelude(path)} ${searchablePathGuard()} ${validatePattern}if test -d "$p"; then find -P "$p" -type f -exec grep -nH${flag} --binary-files=without-match -- ${shellQuote(pattern)} {} +; else ${readableFileGuard()} grep -n${flag} --binary-files=without-match -- ${shellQuote(pattern)} "$p"; fi | head -n ${maxMatches}`;
    }
    case "process_list": {
      const observation = "printf 'OBSERVED_AT_UTC\\t'; date -u '+%Y-%m-%dT%H:%M:%SZ'";
      const listing = "TZ=UTC LC_ALL=C ps -eo pid=,ppid=,lstart=,etime=,user=,args=";
      if (request.pattern == null || request.pattern === "") return `${observation}; ${listing}`;
      const pattern = boundedText(request.pattern, "pattern", 256);
      return `${observation}; ${listing} | grep -F -- ${shellQuote(pattern)} | grep -v '[g]rep -F' | head -n 200`;
    }
    case "process_detail": {
      const pid = integer(request.pid, 0, 1, 4_194_304, "pid");
      return [
        `pid=${pid}`,
        `test -d "/proc/$pid" || exit 46`,
        "printf 'OBSERVED_AT_UTC\\t'; date -u '+%Y-%m-%dT%H:%M:%SZ'",
        `printf 'PID\\t%s\\n' "$pid"`,
        `printf 'EXE\\t'; readlink -f -- "/proc/$pid/exe" || exit 47`,
        `printf 'CWD\\t'; readlink -f -- "/proc/$pid/cwd" || exit 47`,
        `first=$(tr '\\000' '\\n' < "/proc/$pid/cmdline" | sed -n '1p')`,
        `tr '\\000' '\\n' < "/proc/$pid/cmdline" | sed '/^$/d; s/^/ARG\\t/'`,
        `case "$first" in ''|*[!A-Za-z0-9._+-]*) ;; *) located=$(command -v -- "$first" 2>/dev/null || true); test -z "$located" || { resolved=$(realpath -e -- "$located" 2>/dev/null || true); test -z "$resolved" || printf 'COMMAND\\t%s\\n' "$resolved"; } ;; esac`,
      ].join("; ");
    }
    case "port_list":
      return "ss -lntp";
    case "http_get": {
      const port = integer(request.port, 0, 1, 65_535, "port");
      const path = boundedText(request.path, "path", 2_048);
      if (!path.startsWith("/") || path.startsWith("//") || path.includes("://")) {
        repairValidation("invalid_loopback_path", "http_get 只允许 loopback 绝对 URL path");
      }
      return `curl -fsS --max-time 10 -- ${shellQuote(`http://127.0.0.1:${port}${path}`)}`;
    }
    case "shell_exec": {
      if (typeof request.command !== "string" || !request.command.trim()
        || request.command.length > 16_384 || request.command.includes("\0")) {
        repairValidation("invalid_runtime_command", "command 必须是 1..16384 字符且不含 NUL 的 shell 命令");
      }
      const encoded = Buffer.from(request.command, "utf8").toString("base64");
      return `cd /home/admin 2>/dev/null || cd /; printf %s ${shellQuote(encoded)} | base64 -d | bash --noprofile --norc`;
    }
    default:
      return repairValidation("unsupported_runtime_operation", "不支持的 Repair 只读操作");
  }
}

function safeOutput(value: unknown): string {
  return redactPersistableLines(value, 32 * 1024);
}

// The remote command caps matches before incident-time filtering. Preserve
// that boundary even when filtering subsequently produces an empty result.
function searchCoverage(stdout: string, maxMatches: number, matchMode: "literal" | "regex"): Record<string, unknown> {
  const matchingLinesBeforeTimeFilter = stdout.split(/\r?\n/u)
    .filter(line => line.trim() && line !== "[TRUNCATED]").length;
  const limitReached = matchingLinesBeforeTimeFilter >= maxMatches;
  const outputTruncated = stdout.includes("[TRUNCATED]");
  return {
    maxMatches,
    matchMode,
    patternSyntax: matchMode === "regex" ? "POSIX ERE" : "fixed string; metacharacters are literal",
    matchingLinesBeforeTimeFilter,
    limitReached,
    outputTruncated,
    remainingMatches: "unknown",
    explanation: limitReached || outputTruncated
      ? "返回已达到行数上限或发生输出截断；上限在故障时间过滤前生效，剩余匹配未知。仅代表所示片段，不能据此断言整个故障窗口没有其他请求或错误；过滤后为0行也不代表窗口内无匹配。"
      : "未触及本次匹配行数上限；结果仅覆盖指定路径与所选匹配模式，不证明其他路径或消费链路不存在异常。",
  };
}

type TemporalScope = {
  mode: "incident_window";
  from: string;
  to: string;
  timestampedLines: number;
  retainedLines: number;
  excludedLines: number;
  explanation: string;
};

function isLogLocator(value: string): boolean {
  const normalized = posixPath.normalize(value).toLowerCase();
  return normalized.includes("/log/")
    || normalized.includes("/logs/")
    || normalized.endsWith("/log")
    || normalized.endsWith("/logs")
    || /(?:^|\/)[^/]+\.(?:log|out)(?:\.\d+)?$/u.test(normalized);
}

function timestampMilliseconds(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    const milliseconds = value < 10_000_000_000 ? value * 1_000 : value;
    return Number.isFinite(milliseconds) ? milliseconds : null;
  }
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Date.parse(value.trim());
  return Number.isFinite(parsed) ? parsed : null;
}

function timestampFromLogLine(line: string): number | null {
  const isoMatch = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})/u.exec(line);
  if (isoMatch) return timestampMilliseconds(isoMatch[0]);
  const objectStart = line.indexOf("{");
  if (objectStart < 0) return null;
  try {
    const value = JSON.parse(line.slice(objectStart)) as Record<string, unknown>;
    const metadata = value._meta && typeof value._meta === "object" && !Array.isArray(value._meta)
      ? value._meta as Record<string, unknown>
      : {};
    for (const candidate of [value.time, value.timestamp, value.date, metadata.date]) {
      const parsed = timestampMilliseconds(candidate);
      if (parsed != null) return parsed;
    }
  } catch {
    return null;
  }
  return null;
}

function scopeLogOutputToIncident(
  context: RepairTaskContext,
  operation: string,
  requestLocators: readonly string[],
  stdout: string,
): { stdout: string; temporalScope?: TemporalScope } {
  if (!["fs_read", "fs_search"].includes(operation)
    || requestLocators.length !== 1
    || !isLogLocator(requestLocators[0])) {
    return { stdout };
  }
  const from = context.issue.timeRange.from * 1_000;
  const to = context.issue.timeRange.to * 1_000;
  const lines = stdout.split(/\r?\n/u);
  if (lines.at(-1) === "") lines.pop();
  const timestamps = lines.map(timestampFromLogLine);
  const timestampedLines = timestamps.filter((value): value is number => value != null).length;
  if (timestampedLines === 0) return { stdout };

  let inheritedTimestamp: number | null = null;
  const retained = lines.filter((_, index) => {
    const ownTimestamp = timestamps[index];
    if (ownTimestamp != null) inheritedTimestamp = ownTimestamp;
    if (inheritedTimestamp == null) return true;
    return inheritedTimestamp >= from && inheritedTimestamp <= to;
  });
  const retainedTimestampedLines = retained.filter(line => timestampFromLogLine(line) != null).length;
  const excludedLines = lines.length - retained.length;
  return {
    stdout: retained.join("\n"),
    temporalScope: {
      mode: "incident_window",
      from: new Date(from).toISOString(),
      to: new Date(to).toISOString(),
      timestampedLines,
      retainedLines: retainedTimestampedLines,
      excludedLines,
      explanation: excludedLines > 0
        ? "已排除故障时间窗外的日志记录；被排除内容不能用于本次历史故障归因"
        : "返回的带时间戳日志均位于故障时间窗内",
    },
  };
}

const EVIDENCE_LOCATOR_RUNTIME_OPERATIONS = new Set([
  "fs_list",
  "fs_find",
  "fs_stat",
  "fs_read",
  "fs_search",
  "process_list",
  "process_detail",
]);

const STRUCTURED_FILESYSTEM_OPERATIONS = new Set([
  "fs_list",
  "fs_find",
  "fs_stat",
  "fs_read",
  "fs_search",
]);

function isBroadFilesystemLocator(value: string): boolean {
  const normalized = posixPath.normalize(value);
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length < 2) return true;
  return parts.length === 2 && parts[0] === "home";
}

function canonicalStatLocators(stdout: string): string[] {
  return [...new Set(stdout.split(/\r?\n/u).flatMap((line) => {
    const fields = line.split("\t");
    if (fields.length !== 6) return [];
    const rawCandidate = fields[5];
    if (!rawCandidate.startsWith("/") || /[\u0000-\u001f\u007f]/u.test(rawCandidate)
      || rawCandidate.split("/").some(segment => segment === "." || segment === "..")) {
      return [];
    }
    const candidate = posixPath.normalize(rawCandidate);
    return isBroadFilesystemLocator(candidate) ? [] : [candidate];
  }))];
}

function verifiedOutputLocators(
  operation: string,
  stdout: string,
  stderr: string,
  requestLocators: readonly string[],
): string[] {
  // Shell and HTTP output are controlled by the requested command/endpoint and
  // can echo or synthesize paths. They are useful evidence text, but are not a
  // trusted producer of new filesystem locators. Structured filesystem calls
  // are provenance-gated by the Wrapper; process metadata independently
  // reports locators belonging to the affected process.
  if (!EVIDENCE_LOCATOR_RUNTIME_OPERATIONS.has(operation)) return [];
  if (operation === "process_detail") {
    return [...new Set(stdout.split(/\r?\n/u).flatMap((line) => {
      const match = /^(EXE|CWD|ARG|COMMAND)\t(.+)$/u.exec(line);
      return match ? evidenceLocatorsFromText(match[2]) : [];
    }))];
  }
  if (operation === "fs_stat") return canonicalStatLocators(stdout);
  if (STRUCTURED_FILESYSTEM_OPERATIONS.has(operation)) {
    if (operation !== "fs_list" || requestLocators.length !== 1) {
      return [...new Set(requestLocators)];
    }
    const requestedRoot = posixPath.normalize(requestLocators[0]).replace(/\/+$/u, "") || "/";
    const listedChildren = stdout.split(/\r?\n/u).flatMap((line) => {
      // fs_list is emitted by the server-owned `find -printf '%y\t%p'`
      // command. Only direct regular-file/directory children are navigable;
      // symlinks and malformed/nested output cannot mint new locators.
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
  if (operation !== "shell_exec") return [];
  const observed = new Set(evidenceLocatorsFromText(stdout, stderr));
  for (const line of `${stdout}\n${stderr}`.split(/\r?\n/u)) {
    const fileMatch = /^(\/[^\r\n:]+): symbolic link to ([^\s\r\n]+)$/u.exec(line);
    const listMatch = /(?:^|\s)(\/[^\s\r\n]+)\s+->\s+([^\s\r\n]+)$/u.exec(line);
    const match = fileMatch ?? listMatch;
    if (!match) continue;
    const [, linkPath, rawTarget] = match;
    const resolvedTarget = rawTarget.startsWith("/")
      ? posixPath.normalize(rawTarget)
      : posixPath.resolve(posixPath.dirname(linkPath), rawTarget);
    observed.add(posixPath.normalize(linkPath));
    observed.add(resolvedTarget);
  }
  return [...observed];
}

export class RepairRuntimeTool {
  constructor(
    private readonly config: ResolvedBaasConfig,
    private readonly arcaTransport?: ArcaCommandTransport,
  ) {}

  async inspect(
    context: RepairTaskContext,
    request: RepairRuntimeInspectInput,
  ): Promise<Record<string, unknown>> {
    assertRepairPlanShellIsObservational(context, request);
    const requestFields = request as RepairRuntimeInspectInput & {
      path?: unknown;
      pattern?: unknown;
      command?: unknown;
    };
    const requestLocators = STRUCTURED_FILESYSTEM_OPERATIONS.has(request.operation)
      ? evidenceLocatorsFromText(typeof requestFields.path === "string" ? requestFields.path : "")
      : [];
    return this.execute(
      context,
      request.operation,
      buildRepairRuntimeCommand(request),
      requestLocators,
      request.operation === "fs_search" ? integer(request.maxMatches, 200, 1, 500, "maxMatches") : undefined,
      request.operation === "fs_search" ? searchMatchMode(request.matchMode) : undefined,
    );
  }

  async applyApprovedAction(
    context: RepairTaskContext,
    action: RepairPlanAction,
  ): Promise<Record<string, unknown>> {
    if (action.type !== "container_command" || !action.command) {
      repairValidation("unsupported_repair_action", "当前只支持执行获批的 container_command");
    }
    if (action.command.length > 16_384 || action.command.includes("\0")) {
      repairValidation("invalid_approved_command", "获批 command 格式不合法");
    }
    const encoded = Buffer.from(action.command, "utf8").toString("base64");
    return this.execute(
      context,
      `apply_action:${action.actionId}`,
      `printf %s ${shellQuote(encoded)} | base64 -d | bash`,
      evidenceLocatorsFromText(action.command),
    );
  }

  private async execute(
    context: RepairTaskContext,
    operation: string,
    command: string,
    requestLocators: readonly string[],
    searchMaxMatches?: number,
    matchMode: "literal" | "regex" = "literal",
  ): Promise<Record<string, unknown>> {
    const executionContextBinding = operation === "execution_context"
      ? { executionContextBinding: repairExecutionContextBinding(context, REPAIR_RUNTIME_USER) }
      : {};
    const observationStartedAt = new Date().toISOString();
    const incidentWindow = {
      from: new Date(context.issue.timeRange.from * 1_000).toISOString(),
      to: new Date(context.issue.timeRange.to * 1_000).toISOString(),
    };
    const target = context.target;
    const runtimeUserCommand = buildRepairRuntimeUserCommand(command);
    if (target.provider === "arca") {
      if (!target.sandboxId) repairValidation("runtime_target_missing", "Repair 缺少 ARCA sandbox_id");
      if (!this.arcaTransport) {
        return repairUnavailable("repair_arca_not_configured", "Repair 未配置 ARCA 运行态访问");
      }
      const result = await this.arcaTransport.execute({
        environment: target.environment,
        bindingId: target.bindingId,
        sandboxId: target.sandboxId,
        arcaInstanceId: target.arcaInstanceId,
        command: runtimeUserCommand,
      });
      const unscopedStdout = safeOutput(result.stdout);
      const scopedOutput = scopeLogOutputToIncident(
        context,
        operation,
        requestLocators,
        unscopedStdout,
      );
      const stdout = scopedOutput.stdout;
      const stderr = safeOutput(result.stderr);
      const observationFinishedAt = new Date().toISOString();
      return {
        status: result.status,
        operation,
        target: {
          environment: target.environment,
          bindingId: target.bindingId,
          sandboxId: target.sandboxId,
        },
        exitCode: result.exitCode,
        stdout,
        stderr,
        ...(searchMaxMatches == null ? {} : { searchCoverage: searchCoverage(unscopedStdout, searchMaxMatches, matchMode) }),
        evidenceLocators: result.status === "success"
          ? verifiedOutputLocators(operation, stdout, stderr, requestLocators)
          : [],
        shellObservedLocators: result.status === "success"
          ? shellObservedLocators(operation, stdout, stderr)
          : [],
        durationMs: result.durationMs,
        observation: {
          kind: "repair_probe",
          ...executionContextBinding,
          startedAt: observationStartedAt,
          finishedAt: observationFinishedAt,
          incidentWindow,
          causality: "本结果由该时间窗内的 Repair 探针产生；探针期间新出现的进程或日志不能在缺少独立关联证据时解释更早发生的故障",
          ...(scopedOutput.temporalScope ? { temporalScope: scopedOutput.temporalScope } : {}),
        },
      };
    }
    if (target.provider !== "baas") {
      throw new RepairError(422, "unsupported_runtime_provider", `Repair 当前不能操作 provider=${target.provider}`);
    }
    if (!target.deviceId) {
      repairValidation("runtime_target_missing", "Repair 缺少 BaaS 逻辑 deviceId");
    }
    let targetConfig: BaasCommandTargetConfig;
    try {
      targetConfig = resolveBaasCommandTarget(this.config, target.environment);
    } catch (error) {
      return repairUnavailable(
        "repair_baas_not_configured",
        `Repair 无法复用 ${target.environment} BaaS 配置: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), (this.config.commandTimeoutSeconds + 5) * 1_000);
    try {
      let transportResult: Awaited<ReturnType<typeof executeBaasCommand<BaasCommandResponse>>> | null = null;
      const maxTransportAttempts = context.phase === "repair_plan" && operation !== "shell_exec" ? 2 : 1;
      for (let attempt = 1; attempt <= maxTransportAttempts; attempt += 1) {
        try {
          transportResult = await executeBaasCommand<BaasCommandResponse>({
            config: targetConfig,
            tenant: this.config.commandTenant,
            deviceId: target.deviceId,
            deviceAffinity: context.taskId,
            cmd: runtimeUserCommand,
            timeoutSeconds: this.config.commandTimeoutSeconds,
            signal: controller.signal,
          });
          if (transportResult.response.headers.get("content-type")?.includes("text/html")) {
            throw new Error("BaaS 本地身份未生效，服务返回登录页");
          }
          break;
        } catch (error) {
          const isRejectedLocalIdentity = error instanceof Error
            && error.message === "BaaS 本地身份未生效，服务返回登录页";
          if (!isRejectedLocalIdentity || attempt === maxTransportAttempts) throw error;
        }
      }
      if (!transportResult) throw new Error("BaaS 操作未返回结果");
      const { response, body } = transportResult;
      const data = body.data ?? {};
      const exitCode = data.exit_code ?? data.result?.exit_code ?? null;
      const succeeded = response.ok
        && (body.code == null || Number(body.code) === 0)
        && body.data != null
        && (exitCode == null || exitCode === 0);
      const unscopedStdout = safeOutput(data.stdout ?? data.result?.stdout ?? "");
      const scopedOutput = scopeLogOutputToIncident(
        context,
        operation,
        requestLocators,
        unscopedStdout,
      );
      const stdout = scopedOutput.stdout;
      const stderr = safeOutput(data.stderr ?? data.result?.stderr ?? body.buserviceErrorMsg ?? body.message ?? "");
      const observationFinishedAt = new Date().toISOString();
      return {
        status: succeeded ? "success" : "failed",
        operation,
        target: {
          environment: target.environment,
          bindingId: target.bindingId,
          deviceId: target.deviceId,
        },
        exitCode,
        stdout,
        stderr,
        ...(searchMaxMatches == null ? {} : { searchCoverage: searchCoverage(unscopedStdout, searchMaxMatches, matchMode) }),
        evidenceLocators: succeeded
          ? verifiedOutputLocators(operation, stdout, stderr, requestLocators)
          : [],
        shellObservedLocators: succeeded
          ? shellObservedLocators(operation, stdout, stderr)
          : [],
        durationMs: data.execution_time_ms ?? null,
        observation: {
          kind: "repair_probe",
          ...executionContextBinding,
          startedAt: observationStartedAt,
          finishedAt: observationFinishedAt,
          incidentWindow,
          causality: "本结果由该时间窗内的 Repair 探针产生；探针期间新出现的进程或日志不能在缺少独立关联证据时解释更早发生的故障",
          ...(scopedOutput.temporalScope ? { temporalScope: scopedOutput.temporalScope } : {}),
        },
      };
    } catch (error) {
      if (controller.signal.aborted) {
        return {
          status: "unknown",
          operation,
          error: "BaaS 操作超时；远端执行状态未知",
          observation: {
            kind: "repair_probe",
            ...executionContextBinding,
            startedAt: observationStartedAt,
            finishedAt: new Date().toISOString(),
            incidentWindow,
            causality: "本结果由该时间窗内的 Repair 探针产生；探针期间新出现的进程或日志不能在缺少独立关联证据时解释更早发生的故障",
          },
        };
      }
      if (error instanceof RepairError) throw error;
      throw new RepairError(
        502,
        "repair_baas_failed",
        `BaaS 操作失败: ${redactText(error instanceof Error ? error.message : String(error), 2_000)}`,
      );
    } finally {
      clearTimeout(timeout);
    }
  }

}
