import { posix as posixPath } from "node:path";
import type { ResolvedBaasConfig } from "../../db.js";
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
} from "../baas-command-transport.js";
import { RepairError, repairUnavailable, repairValidation } from "./errors.js";
import { redactPersistableLines, redactText } from "./redaction.js";
import type { ArcaCommandTransport } from "./arca-command-transport.js";
import { evidenceLocatorsFromText } from "./evidence-locators.js";

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function integer(value: unknown, fallback: number, min: number, max: number, field: string): number {
  const normalized = value == null ? fallback : Number(value);
  if (!Number.isSafeInteger(normalized) || normalized < min || normalized > max) {
    repairValidation("invalid_runtime_argument", `${field} 必须在 ${min} 到 ${max} 之间`);
  }
  return normalized;
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

export function buildRepairRuntimeCommand(request: RepairRuntimeInspectInput): string {
  const unsafe = request as RepairRuntimeInspectInput & {
    command?: unknown; cmd?: unknown; url?: unknown; method?: unknown;
  };
  if ((request.operation !== "shell_exec" && unsafe.command != null)
    || unsafe.cmd != null || unsafe.url != null || unsafe.method != null) {
    repairValidation("raw_runtime_command_forbidden", "Repair 只读工具不接受原始 command、URL 或 HTTP method");
  }

  switch (request.operation) {
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
      return `${resolvedPathPrelude(path)} ${searchablePathGuard()} if test -d "$p"; then find -P "$p" -type f -exec grep -nHF --binary-files=without-match -- ${shellQuote(pattern)} {} +; else ${readableFileGuard()} grep -nF --binary-files=without-match -- ${shellQuote(pattern)} "$p"; fi | head -n ${maxMatches}`;
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

export class RepairRuntimeTool {
  constructor(
    private readonly config: ResolvedBaasConfig,
    private readonly arcaTransport?: ArcaCommandTransport,
  ) {}

  async inspect(
    context: RepairTaskContext,
    request: RepairRuntimeInspectInput,
    authHeaders: Record<string, string> = {},
  ): Promise<Record<string, unknown>> {
    const requestFields = request as RepairRuntimeInspectInput & { path?: unknown };
    const requestLocators = STRUCTURED_FILESYSTEM_OPERATIONS.has(request.operation)
      ? evidenceLocatorsFromText(typeof requestFields.path === "string" ? requestFields.path : "")
      : [];
    return this.execute(
      context,
      request.operation,
      buildRepairRuntimeCommand(request),
      requestLocators,
      authHeaders,
    );
  }

  async applyApprovedAction(
    context: RepairTaskContext,
    action: RepairPlanAction,
    authHeaders: Record<string, string> = {},
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
      authHeaders,
    );
  }

  private async execute(
    context: RepairTaskContext,
    operation: string,
    command: string,
    requestLocators: readonly string[],
    authHeaders: Record<string, string>,
  ): Promise<Record<string, unknown>> {
    const target = context.target;
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
        command,
        authHeaders,
      });
      const stdout = safeOutput(result.stdout);
      const stderr = safeOutput(result.stderr);
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
        evidenceLocators: result.status === "success"
          ? verifiedOutputLocators(operation, stdout, stderr, requestLocators)
          : [],
        shellObservedLocators: result.status === "success"
          ? shellObservedLocators(operation, stdout, stderr)
          : [],
        durationMs: result.durationMs,
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
      const { response, body } = await executeBaasCommand<BaasCommandResponse>({
        config: targetConfig,
        tenant: this.config.commandTenant,
        deviceId: target.deviceId,
        deviceAffinity: context.taskId,
        cmd: command,
        timeoutSeconds: this.config.commandTimeoutSeconds,
        signal: controller.signal,
      });
      const data = body.data ?? {};
      const exitCode = data.exit_code ?? data.result?.exit_code ?? null;
      const succeeded = response.ok
        && (body.code == null || Number(body.code) === 0)
        && body.data != null
        && (exitCode == null || exitCode === 0);
      const stdout = safeOutput(data.stdout ?? data.result?.stdout ?? "");
      const stderr = safeOutput(data.stderr ?? data.result?.stderr ?? body.buserviceErrorMsg ?? body.message ?? "");
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
        evidenceLocators: succeeded
          ? verifiedOutputLocators(operation, stdout, stderr, requestLocators)
          : [],
        shellObservedLocators: succeeded
          ? shellObservedLocators(operation, stdout, stderr)
          : [],
        durationMs: data.execution_time_ms ?? null,
      };
    } catch (error) {
      if (controller.signal.aborted) {
        return { status: "unknown", operation, error: "BaaS 操作超时；远端执行状态未知" };
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
