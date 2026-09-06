import {
  resolveBaasConfig,
  type ResolvedBaasConfig,
} from "@avernet/clawweb-shared/server/db";
import { getCurrentEnv } from "../env.js";
import type { EvolveBotRuntime } from "../repositories/evolve-repository.js";
import { sendIntervention } from "./baas-intervention.js";
import { readDiagnoseJudgeBackend, redactSecret } from "./evolve/command.js";
import {
  baasRequestHeaders,
  executeBaasCommand,
  resolveBaasCommandTarget,
  type BaasCommandEnvironment,
  type BaasCommandTargetConfig,
} from "./baas-command-transport.js";
import { evolveStepBaasStage, stepUsesBaasRuntime } from "./evolve/task-registry.js";

type BotPlatformResponse = {
  code?: number;
  data?: {
    run_id?: string; session_id?: string; message_id?: string;
    exit_code?: number; stdout?: string; stderr?: string; execution_time_ms?: number;
  };
  run_id?: string; session_id?: string;
  message?: string; trace_id?: string; buserviceErrorMsg?: string; buserviceErrorCode?: string;
  error?: string | { message?: string; code?: string };
  evolve_dispatch?: {
    provider: string; transport: "baas_execute_command" | "message";
    environment?: EvolveBaasEnvironment;
    release_lane?: ReturnType<typeof getCurrentEnv>;
    runner_status?: "started" | "running" | "already_started";
    runner_mode?: "direct";
  };
};

export type EvolveBaasEnvironment = BaasCommandEnvironment;
export type EvolveBaasTargetConfig = BaasCommandTargetConfig;
export type EvolveRunnerConfig = {
  environment: ReturnType<typeof getCurrentEnv>;
  evolveScriptPath: string;
};

export type OptimizeDispatchArgs = {
  round: number;
  trainBenchDomainId?: string;
  testBenchDomainId?: string;
};

export type EvolveDispatchSecrets = {
  diagnoseApiKey?: string;
};

export type EvolveDispatchInput = {
  taskId: string; stepPk: number; stepId: string; stepType: string;
  userId: string; botId: string; command: string; mode: "message" | "run";
  callbackUrl?: string;
  runtime?: EvolveBotRuntime | null;
  optimizeArgs?: OptimizeDispatchArgs;
  forceMessage?: boolean;
  runtimeMaintenance?: boolean;
  secrets?: EvolveDispatchSecrets;
};

export type EvolveTaskLogDispatchInput = {
  taskId: string; archiveId: string; userId: string; botId: string;
  callbackUrl: string; clawwebUrl: string; runtime: EvolveBotRuntime;
};

const RUNTIME_MAINTENANCE_DISABLED_STEPS = new Set(["pack", "restore", "runtime_cleanup"]);
const ARCA_DIRECT_RUNNER_STEPS = new Set([
  "diagnose", "plan", "bench", "bench_plan", "optimize", "pack", "restore", "runtime_cleanup",
]);

export function resolveRuntimeMaintenance(stepType: string, requested?: boolean): boolean {
  return !RUNTIME_MAINTENANCE_DISABLED_STEPS.has(stepType) && requested !== false;
}

export type EvolveCancelInput = {
  taskId: string; stepId: string; stepType: string; userId: string; botId: string;
  sessionId: string | null; platformResponse: unknown;
  runtime?: EvolveBotRuntime | null;
};

type DispatchResult = {
  runId: string | null;
  sessionId: string | null;
  platformResponse: BotPlatformResponse;
};

class EvolveDispatchValidationError extends Error {}
const MAX_EVOLVE_COMMAND_BYTES = 64 * 1024;
const SAFE_EVOLVE_SCRIPT_PATH = /^\/[A-Za-z0-9._/-]+$/;
export function resolveEvolveTransport(input: Pick<EvolveDispatchInput, "stepType" | "runtime" | "forceMessage">): "baas_execute_command" | "message" {
  return input.runtime?.provider === "baas" && stepUsesBaasRuntime(input.stepType)
    ? "baas_execute_command"
    : "message";
}

export function usesArcaDirectRunner(input: Pick<EvolveDispatchInput, "stepType" | "runtime" | "forceMessage">): boolean {
  return input.runtime?.provider?.toLowerCase() === "arca"
    && resolveEvolveTransport(input) === "message"
    && ARCA_DIRECT_RUNNER_STEPS.has(input.stepType);
}

export function resolveEvolveBaasTargetConfig(
  config: ResolvedBaasConfig,
  runtimeEnv: string | null | undefined,
): EvolveBaasTargetConfig {
  let target: EvolveBaasTargetConfig;
  try {
    target = resolveBaasCommandTarget(config, runtimeEnv);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new EvolveDispatchValidationError(
      message.includes("环境缺失或不支持") ? `${message}，禁止回落到生产消息平台` : message,
    );
  }
  return target;
}

export function resolveEvolveRunnerConfig(
  config: ResolvedBaasConfig,
  controlEnvironment: ReturnType<typeof getCurrentEnv> = getCurrentEnv(),
): EvolveRunnerConfig {
  const evolveScriptPath = config.evolveScriptPaths[controlEnvironment]?.trim();
  const pathSegments = evolveScriptPath?.split("/") ?? [];
  if (!evolveScriptPath
    || !SAFE_EVOLVE_SCRIPT_PATH.test(evolveScriptPath)
    || pathSegments.includes("..")
    || (controlEnvironment !== "dev" && !evolveScriptPath.includes(`/clawevolve/${controlEnvironment}/`))) {
    throw new EvolveDispatchValidationError(`ClawWeb 未配置合法的 ${controlEnvironment} evolveScriptPath`);
  }
  return {
    evolveScriptPath,
    environment: controlEnvironment,
  };
}

export function buildBaasEvolveCommand(input: EvolveDispatchInput, scriptPath: string): string {
  if (!SAFE_EVOLVE_SCRIPT_PATH.test(scriptPath) || scriptPath.split("/").includes("..")) {
    throw new EvolveDispatchValidationError("BaaS evolveScriptPath 必须是绝对路径");
  }
  const command = input.command.trim();
  if (!/^\/claw[^\s]*/.test(command)
    || Buffer.byteLength(command, "utf8") > MAX_EVOLVE_COMMAND_BYTES || /[\0\r\n]/.test(command)) {
    throw new EvolveDispatchValidationError(`BaaS ${input.stepType} 指令非法`);
  }
  const separator = command.search(/\s/);
  let args = separator < 0 ? "" : command.slice(separator).trim();
  if (input.stepType === "bench_plan") args = args.replace(/^--stage\s+bench-plan(?:\s+|$)/, "");
  if (input.stepType === "optimize") args = args.replace(/^--stage\s+optimize(?:\s+|$)/, "");
  const encoded = Buffer.from(args, "utf8").toString("base64");
  const stage = evolveStepBaasStage(input.stepType);
  if (!stage) throw new EvolveDispatchValidationError(`BaaS 不支持 ${input.stepType} 阶段`);
  const runtimeMaintenance = resolveRuntimeMaintenance(input.stepType, input.runtimeMaintenance);
  return `CLAWEVOLVE_RUNTIME_MAINTENANCE=${runtimeMaintenance} bash ${scriptPath} --stage ${stage} --args-base64 '${encoded}'`;
}

export function buildArcaRunnerMessage(
  input: EvolveDispatchInput,
  config: ResolvedBaasConfig = resolveBaasConfig(),
): string {
  if (!usesArcaDirectRunner(input)) {
    throw new EvolveDispatchValidationError(`ARCA Direct Runner 不支持 ${input.stepType} 阶段`);
  }
  if (Object.values(input.secrets ?? {}).some((value) => String(value ?? "").trim())) {
    throw new EvolveDispatchValidationError("ARCA Direct Runner 禁止传递 API Key 或 secrets");
  }
  if (input.stepType === "diagnose" && readDiagnoseJudgeBackend(input.command) === "api") {
    throw new EvolveDispatchValidationError("ARCA 模式只支持 Agent Judge，不支持 API Judge");
  }
  if (/(?:^|\s)--debug(?:=|\s+)true(?:\s|$)/i.test(input.command)) {
    throw new EvolveDispatchValidationError("ARCA Direct Runner 不支持 --debug true");
  }
  const runner = resolveEvolveRunnerConfig(config);
  const command = buildBaasEvolveCommand(input, runner.evolveScriptPath);
  const encoded = command.match(/--args-base64 '([^']*)'/)?.[1];
  const original = input.command.trim();
  const separator = original.search(/\s/);
  let expectedArgs = separator < 0 ? "" : original.slice(separator).trim();
  if (input.stepType === "bench_plan") expectedArgs = expectedArgs.replace(/^--stage\s+bench-plan(?:\s+|$)/, "");
  if (input.stepType === "optimize") expectedArgs = expectedArgs.replace(/^--stage\s+optimize(?:\s+|$)/, "");
  if (!encoded || Buffer.from(encoded, "base64").toString("utf8") !== expectedArgs) {
    throw new EvolveDispatchValidationError("ARCA Runner 参数 Base64 回读校验失败");
  }
  const message = [
    "这是 ClawEvolve 系统任务。",
    "必须使用 exec 工具原样执行下面唯一一条命令，不得添加 sudo，不得修改参数，不得执行其他命令。",
    "执行完成后只返回脚本 stdout 的最后一行 JSON。",
    "",
    command,
  ].join("\n");
  if (Buffer.byteLength(message, "utf8") > MAX_EVOLVE_COMMAND_BYTES) {
    throw new EvolveDispatchValidationError(`ARCA ${input.stepType} Runner Message 超过 ${MAX_EVOLVE_COMMAND_BYTES} 字节`);
  }
  return message;
}

type RunnerStartResult = {
  ok: true;
  status: "started" | "running" | "already_started";
  task_id: string;
  step_id: string;
  pid: string | number;
};

type RunnerIdentity = Pick<EvolveDispatchInput, "taskId" | "stepId">;

function parseRunnerStartResult(stdout: string | undefined, input: RunnerIdentity): RunnerStartResult {
  const lines = String(stdout ?? "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  let parsed: unknown;
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    try {
      parsed = JSON.parse(lines[index]);
      break;
    } catch {
      // Ignore non-JSON launcher noise and require one valid result line.
    }
  }
  const result = parsed as Partial<RunnerStartResult> | undefined;
  const pid = String(result?.pid ?? "");
  if (result?.ok !== true
    || !new Set(["started", "running", "already_started"]).has(String(result.status))
    || result.task_id !== input.taskId
    || result.step_id !== input.stepId
    || !/^[1-9]\d*$/.test(pid)) {
    throw new Error("BaaS Runner 启动结果非法");
  }
  return result as RunnerStartResult;
}

function callbackRunnerTexts(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  const record = value as Record<string, unknown>;
  const texts: string[] = [];
  if (record.ok === true && record.status && record.task_id && record.step_id && record.pid != null) {
    texts.push(JSON.stringify(record));
  }
  for (const key of ["stdout", "text", "content", "finalAssistantVisibleText", "finalAssistantRawText"]) {
    if (typeof record[key] === "string") texts.push(String(record[key]));
  }
  for (const key of ["payloads", "result", "data", "output"]) {
    const child = record[key];
    if (Array.isArray(child)) texts.push(...child.flatMap(callbackRunnerTexts));
    else texts.push(...callbackRunnerTexts(child));
  }
  return texts;
}

export function parseArcaRunnerCallback(
  result: unknown,
  metadata: unknown,
  input: RunnerIdentity,
): RunnerStartResult | null {
  for (const text of [...callbackRunnerTexts(result), ...callbackRunnerTexts(metadata)]) {
    try { return parseRunnerStartResult(text, input); }
    catch { /* Try the next structured Callback field. */ }
  }
  return null;
}
export async function cancelEvolveExecution(input: EvolveCancelInput): Promise<{ transport: string }> {
  const response = input.platformResponse && typeof input.platformResponse === "object"
    ? input.platformResponse as BotPlatformResponse : {};
  const transport = input.runtime
    ? resolveEvolveTransport({ stepType: input.stepType, runtime: input.runtime })
    : response.evolve_dispatch?.transport ?? "message";
  if (transport === "baas_execute_command") {
    const runtime = input.runtime;
    if (!runtime?.deviceId) throw new Error("BaaS Bot 缺少 device_id，无法停止运行");
    const config = resolveBaasConfig();
    const target = resolveEvolveBaasTargetConfig(config, runtime.env);
    const runner = resolveEvolveRunnerConfig(config);
    const cmd = `bash ${runner.evolveScriptPath} --stage stop --args-base64 '${Buffer.from(`--task-id ${input.taskId} --step-id ${input.stepId}`, "utf8").toString("base64")}'`;
    const { response: result, body } = await executeBaasCommand<BotPlatformResponse>({
      config: target,
      tenant: config.commandTenant,
      deviceId: runtime.deviceId,
      deviceAffinity: input.taskId,
      cmd,
      timeoutSeconds: 15,
    });
    if (!result.ok || (body.code != null && body.code !== 0) || (body.data?.exit_code != null && body.data.exit_code !== 0)) {
      throw new Error(`BaaS 停止失败: ${body.data?.stderr || body.message || JSON.stringify(body).slice(0, 500)}`);
    }
    return { transport };
  }
  if (!input.sessionId) throw new Error("Message Step 缺少 session_id，无法向同一会话发送停止指令");
  const effectiveOwnerId = input.runtime?.ownerId || input.userId;
  const routedBotId = input.botId.endsWith(`:${effectiveOwnerId}`) ? input.botId : `${input.botId}:${effectiveOwnerId}`;
  let message = `终止当前任务。任务 ID: ${input.taskId}，Step ID: ${input.stepId}。请立即停止当前执行，不再继续后续操作。`;
  if (response.evolve_dispatch?.provider?.toLowerCase() === "arca"
    && response.evolve_dispatch.runner_mode === "direct") {
    if (!/^[A-Za-z0-9._:-]{1,256}$/.test(input.taskId) || !/^[A-Za-z0-9._:-]{1,256}$/.test(input.stepId)) {
      throw new Error("ARCA Runner stop 参数非法");
    }
    const runner = resolveEvolveRunnerConfig(resolveBaasConfig());
    const stopArgs = `--task-id ${input.taskId} --step-id ${input.stepId}`;
    const stopCommand = `bash ${runner.evolveScriptPath} --stage stop --args-base64 '${Buffer.from(stopArgs, "utf8").toString("base64")}'`;
    message = [
      "这是 ClawEvolve 系统停止任务。",
      "必须使用 exec 工具原样执行下面唯一一条命令，不得添加 sudo，不得修改参数，不得执行其他命令。",
      "执行完成后只返回脚本 stdout 的最后一行 JSON。",
      "",
      stopCommand,
    ].join("\n");
  }
  const result = await sendIntervention({
    botId: routedBotId, sessionKey: input.sessionId, sessionId: input.sessionId,
    message,
    lifecycleStage: "draft",
    transportConfig: resolveEvolveBaasTargetConfig(resolveBaasConfig(), input.runtime?.env),
  });
  if (!result.ok) throw new Error(result.error || "同会话停止消息发送失败");
  return { transport };
}

async function dispatchBaasCommand(input: EvolveDispatchInput): Promise<DispatchResult> {
  const runtime = input.runtime;
  if (!runtime?.bindingId || !runtime.deviceId) {
    throw new Error(`BaaS Bot 缺少草稿态 binding_id 或 device_id，无法执行 ${input.stepType}`);
  }
  if (runtime.botStatus?.toLowerCase() !== "active" || runtime.bindingStatus?.toLowerCase() !== "active") {
    throw new Error(`BaaS Bot 或草稿态绑定未就绪（bot=${runtime.botStatus ?? "unknown"}, binding=${runtime.bindingStatus ?? "unknown"}）`);
  }
  const config = resolveBaasConfig();
  const target = resolveEvolveBaasTargetConfig(config, runtime.env);
  const runnerConfig = resolveEvolveRunnerConfig(config);
  const cmd = buildBaasEvolveCommand(input, runnerConfig.evolveScriptPath);
  const diagnoseApiKey = input.stepType === "diagnose"
    ? String(input.secrets?.diagnoseApiKey ?? "").trim()
    : "";
  const diagnoseJudgeBackend = input.stepType === "diagnose"
    ? readDiagnoseJudgeBackend(input.command)
    : undefined;
  if (diagnoseJudgeBackend === "api" && !diagnoseApiKey) {
    throw new Error("BaaS Diagnose 缺少 LLM API Key");
  }
  const env = diagnoseJudgeBackend === "api" ? { OPENAI_API_KEY: diagnoseApiKey } : undefined;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), (config.commandTimeoutSeconds + 5) * 1000);
  try {
    const { response, body } = await executeBaasCommand<BotPlatformResponse>({
      config: target,
      tenant: config.commandTenant,
      deviceId: runtime.deviceId,
      deviceAffinity: input.taskId,
      cmd,
      timeoutSeconds: config.commandTimeoutSeconds,
      env,
      signal: controller.signal,
    });
    const exitCode = body.data?.exit_code;
    if (!response.ok || (body.code != null && body.code !== 0) || (exitCode != null && exitCode !== 0)) {
      const detail = body.data?.stderr || body.buserviceErrorMsg || body.message
        || (typeof body.error === "string" ? body.error : body.error?.message)
        || JSON.stringify(body).slice(0, 500);
      throw new Error(`BaaS execute-command ${response.status}: ${String(redactSecret(detail || "请求失败", diagnoseApiKey))}`);
    }
    const runner = parseRunnerStartResult(body.data?.stdout, input);
    return {
      runId: `baas:${input.stepId}`,
      sessionId: null,
      platformResponse: {
        code: body.code,
        message: body.message,
        trace_id: body.trace_id,
        data: {
          exit_code: exitCode,
          execution_time_ms: body.data?.execution_time_ms,
          stdout: String(redactSecret(body.data?.stdout?.slice(0, 500), diagnoseApiKey) ?? ""),
        },
        evolve_dispatch: {
          provider: "baas",
          transport: "baas_execute_command",
          environment: target.environment,
          release_lane: runnerConfig.environment,
          runner_status: runner.status,
        },
      },
    };
  } finally {
    clearTimeout(timeout);
  }
}

const MESSAGE_DISPATCH_TIMEOUT_MS = 60_000;

async function dispatchMessage(input: EvolveDispatchInput): Promise<DispatchResult> {
  const config = resolveBaasConfig();
  const target = resolveEvolveBaasTargetConfig(config, input.runtime?.env);
  if (!target.apiKey) throw new Error(`Bot 消息平台未配置 ${target.environment} apiKey`);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), MESSAGE_DISPATCH_TIMEOUT_MS);
  const effectiveOwnerId = input.runtime?.ownerId || input.userId;
  const routedBotId = input.botId.endsWith(`:${effectiveOwnerId}`) ? input.botId : `${input.botId}:${effectiveOwnerId}`;
  console.info(`[clawweb][evolve][dispatchMessage] sending message`, {
    baseUrl: target.baseUrl,
    environment: target.environment,
    botId: routedBotId,
    stepId: input.stepId,
    callbackUrl: input.callbackUrl,
  });
  try {
    let response: Response;
    try {
      response = await fetch(`${target.baseUrl}/openapi/v1/messages`, {
        method: "POST",
        headers: baasRequestHeaders(target),
        body: JSON.stringify({
          bot_id: routedBotId,
          message: input.command,
          ...(input.callbackUrl ? { callback_url: input.callbackUrl } : {}),
          message_id: input.stepId,
          metadata: {
            title: `Claw进化 ${input.stepType === "skill_init" ? "Skill 初始化" : input.stepType} · ${input.taskId} · ${input.stepId}`,
            // ClawEvolve never executes against the published service instance.
            // Make the draft target explicit instead of relying on the platform's
            // default lifecycle (online), including for legacy "message" tasks.
            bot_options: { lifecycle_stage: "draft" },
            sender_options: { from: "owner" }, timeout: 1800, ignore_content: false,
            biz_task_id: input.taskId,
            biz_scene: input.stepType === "skill_init" ? "claw_evolve_init" : "claw_evolve",
          },
        }),
        signal: controller.signal,
      });
    } catch (error) {
      if (controller.signal.aborted) {
        throw new Error(`Bot 平台投递超时（${MESSAGE_DISPATCH_TIMEOUT_MS / 1000}秒）`);
      }
      throw error;
    }
    const body = await response.json().catch(() => ({})) as BotPlatformResponse;
    console.info(`[clawweb][evolve][dispatchMessage] response`, {
      status: response.status,
      code: body.code,
      message: body.message,
      buserviceErrorCode: body.buserviceErrorCode,
      buserviceErrorMsg: body.buserviceErrorMsg,
      runId: body.data?.run_id ?? body.run_id,
      sessionId: body.data?.session_id ?? body.session_id,
    });
    const hasBuserviceError = body.buserviceErrorCode && body.buserviceErrorCode !== "0";
    if (!response.ok || (body.code != null && body.code !== 0) || hasBuserviceError) {
      const nestedError = typeof body.error === "string" ? body.error : body.error?.message;
      const detail = body.buserviceErrorMsg ?? body.message ?? nestedError ?? JSON.stringify(body).slice(0, 500);
      const code = body.buserviceErrorCode ?? (typeof body.error === "object" ? body.error?.code : undefined);
      throw new Error(`Bot 平台 ${response.status}${code ? ` ${code}` : ""}: ${detail || "请求失败"}${body.trace_id ? ` (trace_id=${body.trace_id})` : ""}`);
    }
    return {
      runId: body.data?.run_id ?? body.run_id ?? body.data?.message_id ?? null,
      sessionId: body.data?.session_id ?? body.session_id ?? null,
      platformResponse: {
        ...body,
        evolve_dispatch: {
          provider: input.runtime?.provider ?? "unknown",
          transport: "message",
          environment: target.environment,
        },
      },
    };
  } finally {
    clearTimeout(timeout);
  }
}

async function dispatchArcaRunnerMessage(input: EvolveDispatchInput): Promise<DispatchResult> {
  const config = resolveBaasConfig();
  const runner = resolveEvolveRunnerConfig(config);
  const result = await dispatchMessage({ ...input, command: buildArcaRunnerMessage(input, config) });
  return {
    ...result,
    platformResponse: {
      ...result.platformResponse,
      evolve_dispatch: {
        ...result.platformResponse.evolve_dispatch!,
        provider: "arca",
        transport: "message",
        release_lane: runner.environment,
        runner_mode: "direct",
      },
    },
  };
}

export async function dispatchEvolveCommand(input: EvolveDispatchInput): Promise<DispatchResult> {
  if (resolveEvolveTransport(input) === "baas_execute_command") {
    return dispatchBaasCommand(input);
  }
  if (usesArcaDirectRunner(input)) {
    return dispatchArcaRunnerMessage(input);
  }
  return dispatchMessage(input);
}

function taskLogRunnerPath(evolveScriptPath: string): string {
  const suffix = "/clawevolve_async_runner.sh";
  if (!evolveScriptPath.endsWith(suffix)) {
    throw new EvolveDispatchValidationError("evolveScriptPath 不是受支持的 Release Runner 路径");
  }
  return `${evolveScriptPath.slice(0, -suffix.length)}/clawevolve_task_log_runner.sh`;
}

function buildTaskLogRunnerCommand(input: EvolveTaskLogDispatchInput, evolveScriptPath: string): string {
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(input.taskId)
    || !/^[A-Za-z0-9._:-]{1,128}$/.test(input.archiveId)) {
    throw new EvolveDispatchValidationError("日志归档标识不合法");
  }
  const clawwebUrl = input.clawwebUrl.replace(/\/$/, "");
  if (!/^https?:\/\/[^\s'"`]+$/.test(clawwebUrl)) {
    throw new EvolveDispatchValidationError("日志归档 ClawWeb URL 不合法");
  }
  return `bash ${taskLogRunnerPath(evolveScriptPath)} --task-id ${input.taskId} --archive-id ${input.archiveId} --clawweb-url '${clawwebUrl}'`;
}

export async function dispatchEvolveTaskLogArchive(input: EvolveTaskLogDispatchInput): Promise<DispatchResult> {
  const config = resolveBaasConfig();
  const runner = resolveEvolveRunnerConfig(config);
  const command = buildTaskLogRunnerCommand(input, runner.evolveScriptPath);
  if (input.runtime.provider?.toLowerCase() === "baas") {
    if (!input.runtime.deviceId) throw new Error("BaaS Bot 缺少 device_id，无法获取日志");
    const target = resolveEvolveBaasTargetConfig(config, input.runtime.env);
    const { response, body } = await executeBaasCommand<BotPlatformResponse>({
      config: target,
      tenant: config.commandTenant,
      deviceId: input.runtime.deviceId,
      deviceAffinity: input.taskId,
      cmd: command,
      timeoutSeconds: config.commandTimeoutSeconds,
    });
    if (!response.ok || (body.code != null && body.code !== 0)
      || (body.data?.exit_code != null && body.data.exit_code !== 0)) {
      throw new Error(`BaaS 获取日志投递失败: ${body.data?.stderr || body.message || JSON.stringify(body).slice(0, 500)}`);
    }
    return {
      runId: `baas:${input.archiveId}`,
      sessionId: null,
      platformResponse: {
        ...body,
        evolve_dispatch: {
          provider: "baas", transport: "baas_execute_command",
          environment: target.environment, release_lane: runner.environment, runner_mode: "direct",
        },
      },
    };
  }
  if (input.runtime.provider?.toLowerCase() !== "arca") {
    throw new Error(`当前 Bot 运行环境不支持日志归档: ${input.runtime.provider ?? "unknown"}`);
  }
  const message = [
    "这是 ClawEvolve 系统日志归档任务，不是业务进化节点。",
    "必须使用 exec 工具原样执行下面唯一一条命令，不得添加 sudo，不得修改参数，不得执行其他命令。",
    "执行完成后只返回脚本 stdout 的最后一行 JSON。",
    "",
    command,
  ].join("\n");
  const result = await dispatchMessage({
    taskId: input.taskId,
    stepPk: 0,
    stepId: input.archiveId,
    stepType: "task_log_archive",
    userId: input.userId,
    botId: input.botId,
    command: message,
    mode: "run",
    callbackUrl: input.callbackUrl,
    runtime: input.runtime,
  });
  return {
    ...result,
    platformResponse: {
      ...result.platformResponse,
      evolve_dispatch: {
        ...result.platformResponse.evolve_dispatch!,
        provider: "arca", transport: "message", release_lane: runner.environment, runner_mode: "direct",
      },
    },
  };
}
