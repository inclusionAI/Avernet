import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { closeSync, mkdirSync, openSync, realpathSync } from "node:fs";
import { isAbsolute, join, relative } from "node:path";
import type { EvolveDispatchInput, dispatchEvolveCommand, cancelEvolveExecution } from "../services/evolve-dispatcher.js";
import type { SingleboxConfig } from "./config.js";
import type { LocalBotPaths, ResolveLocalBot } from "./bot-runtime.js";

import { readNodeCommandOption } from "../services/evolve/command.js";

const stages = new Set(["diagnose", "plan", "runtime_cleanup", "bench", "pack", "restore", "bench_plan", "optimize"]);
function selectedModel(config: SingleboxConfig, command: string): string {
  const model = readNodeCommandOption(command, "model") ?? config.model;
  if (!model || model.length > 128 || /[\0\r\n\s]/.test(model)) throw new Error("Invalid local model name");
  return model;
}
function checkedId(value: string): string {
  // COSEC: identifiers are used as path segments, never accept traversal.
  if (!/^[A-Za-z0-9_.-]+$/.test(value) || value.includes("..")) throw new Error("Invalid local task/step ID");
  return value;
}

export function localInvocation(config: SingleboxConfig & LocalBotPaths, input: EvolveDispatchInput) {
  if (!stages.has(input.stepType)) throw new Error("Local stage is not supported");
  // COSEC: bind execution to the resolved personal Bot, not a client-supplied path.
  if (input.userId !== config.userId || input.botId !== config.botId || input.runtime?.env !== config.botEnv) {
    throw new Error("Local Bot binding does not match the task");
  }
  const cleanup = input.stepType === "runtime_cleanup";
  const model = selectedModel(config, input.command);
  const workflow = ["bench_plan", "optimize"].includes(input.stepType);
  const prefix = workflow ? `/clawevolve-workflow --stage ${input.stepType === "bench_plan" ? "bench-plan" : "optimize"}` : input.stepType === "restore" ? "/clawevolve-pack" : cleanup ? "/clawevolve-runtime-cleanup" : `/clawevolve-${input.stepType}`;
  if (!input.command.startsWith(`${prefix} `) || /[\0\r\n]/.test(input.command)
    || Buffer.byteLength(input.command) > 65536
    || /--(?:openclaw-home|output-dir|run-dir|evolve-results-dir|debug-session-path|source|skip-clawweb-report|discovery-notes|target)(?:[\s=]|$)/.test(input.command)) {
    throw new Error("Unsupported local Skill command or path override");
  }
  if (Object.values(input.secrets ?? {}).some(Boolean)) throw new Error("Singlebox V1 uses the Bot model, not API Judge secrets");
  const taskId = checkedId(input.taskId);
  const stepId = checkedId(input.stepId);
  const script = realpathSync(cleanup
    ? join(config.skillsRoot, "scripts/clawevolve_runtime_cleanup.py")
    : ["pack", "restore"].includes(input.stepType)
      ? join(config.skillsRoot, "clawevolve-workflow/scripts/handlers/clawevolve_pack_run.py")
      : workflow
      ? join(config.skillsRoot, `clawevolve-workflow/scripts/handlers/clawevolve_${input.stepType}_run.py`)
      : input.stepType === "bench"
      ? join(config.skillsRoot, "clawevolve-workflow/scripts/handlers/clawevolve_bench_run.py")
      : join(config.skillsRoot, `clawevolve-${input.stepType}`, "scripts/run.sh"));
  const rel = relative(realpathSync(config.skillsRoot), script);
  // COSEC: a symlink must not escape the explicitly selected Skill checkout.
  if (rel.startsWith("..") || isAbsolute(rel)) throw new Error("Skill entry escapes skillsRoot");
  if (cleanup) {
    // COSEC: reconstruct fixed argv, ignoring command text; only this local adapter selects openversion.
    return { executable: "python3", args: ["-u", "-B", script, "--version", "openversion",
      "--openclaw-home", config.openclawHome, "--workspace", config.workspace,
      "--task-id", taskId, "--step-id", stepId, "--force-cleanup",
      "--clawweb-url", `http://127.0.0.1:${config.port}`],
      logDirectory: join(config.dataDirectory, "logs", taskId), stepId };
  }
  if (workflow) {
    const benchPlan = input.stepType === "bench_plan";
    const train = readNodeCommandOption(input.command, benchPlan ? "train-domain-id" : "train-bench-domain-id");
    const test = readNodeCommandOption(input.command, benchPlan ? "test-domain-id" : "test-bench-domain-id");
    if (!train || !test || [train, test].some((value) => value.startsWith("--") || value === "undefined")) {
      throw new Error("Local optimization requires frozen train/test Domains");
    }
    // COSEC: only fixed handlers and known options; preserve business inputs, bind environment locally.
    const args = ["-u", "-B", script, "--task-id", taskId, "--step-id", stepId,
      "--owner-id", config.userId, "--workspace", config.workspace, "--skill-base-dir", config.skillsRoot,
      "--model", model, "--judge", model, "--openclaw-execution-mode", "local",
      "--clawweb-url", `http://127.0.0.1:${config.port}`];
    if (benchPlan) args.push("--train-domain-id", train, "--test-domain-id", test);
    else {
      const round = input.optimizeArgs?.round ?? Number(readNodeCommandOption(input.command, "round"));
      if (!Number.isSafeInteger(round) || round < 1 || round > 100) throw new Error("Invalid optimization round");
      args.push("--action", "run-round", "--round", String(round), "--train-bench-domain-id", train,
        "--test-bench-domain-id", test, "--optimizer-model", model, "--tune-model", model,
        "--review-model", model);
    }
    return { executable: "python3", args, logDirectory: join(config.dataDirectory, "logs", taskId), stepId };
  }
  if (input.stepType === "restore") {
    const source = readNodeCommandOption(input.command, "source-task-id");
    const kind = readNodeCommandOption(input.command, "source-kind");
    const round = readNodeCommandOption(input.command, "source-round") ?? "0";
    if (!source || !["snapshot", "baseline", "round"].includes(kind ?? "") || !/^\d+$/.test(round)
      || !Number.isSafeInteger(Number(round)) || (kind === "round" && Number(round) < 1)) throw new Error("Invalid frozen Pack source");
    // COSEC: fixed argv; Skill rechecks this selection against the frozen server input.
    return { executable: "python3", args: ["-u", "-B", script, "--mode", "restore",
      "--version", "openversion", "--artifact-bucket", "clawevolve-artifacts",
      "--task-id", taskId, "--step-id", stepId, "--source-task-id", checkedId(source),
      "--source-kind", kind!, "--source-round", round, "--clawweb-url", `http://127.0.0.1:${config.port}`],
      logDirectory: join(config.dataDirectory, "logs", taskId), stepId };
  }
  if (input.stepType === "pack") {
    return { executable: "python3", args: ["-u", "-B", script, "--mode", "pack",
      "--task-id", taskId, "--step-id", stepId, "--clawweb-url", `http://127.0.0.1:${config.port}`],
      logDirectory: join(config.dataDirectory, "logs", taskId), stepId };
  }
  if (input.stepType === "bench") {
    const domainId = readNodeCommandOption(input.command, "domain-id");
    if (!domainId) throw new Error("Local Bench requires its frozen Domain");
    // COSEC: only known options become argv; identity, model and callback come from local configuration.
    const args = ["-u", "-B", script, "--task-id", taskId, "--step-id", stepId,
      "--domain-id", domainId, "--owner-id", config.userId, "--model", model,
      "--judge", model, "--openclaw-execution-mode", "local",
      "--clawweb-url", `http://127.0.0.1:${config.port}`];
    for (const name of ["template-name", "template-version", "suite", "scene"] as const) {
      const value = readNodeCommandOption(input.command, name);
      if (value) args.push(`--${name}`, value);
    }
    return { executable: "python3", args, logDirectory: join(config.dataDirectory, "logs", taskId), stepId };
  }
  const results = join(config.workspace, "clawevolve_results");
  const args = [script, input.command, "--task-id", taskId, "--step-id", stepId,
    "--clawweb-url", `http://127.0.0.1:${config.port}`];
  if (input.stepType === "diagnose") {
    args.push("--openclaw-home", config.openclawHome, "--output-dir", join(results, taskId, "diagnose/output"),
      "--judge-backend", "subagent", "--model", model);
  } else {
    args.push("--owner-id", config.userId, "--bot-id", config.botId,
      "--run-dir", join(results, taskId, "diagnose"), "--evolve-results-dir", results);
  }
  return { executable: "bash", args, logDirectory: join(config.dataDirectory, "logs", taskId), stepId };
}

/** Only selected by singlebox.ts. Enterprise dispatch/Runner remains unchanged. */
export function createLocalExecution(
  config: SingleboxConfig,
  onExit: (input: EvolveDispatchInput, message: string) => Promise<void>,
  resolveBot: ResolveLocalBot,
) {
  const active = new Map<string, { child: ChildProcess; done: Promise<void>; botId: string; taskId: string; stepType: string }>();
  let stopping = false;
  const dispatch: typeof dispatchEvolveCommand = async (input) => {
    if (stopping) throw new Error("Singlebox is stopping");
    if (active.has(input.stepId)) throw new Error("Local step is already running");
    const target = await resolveBot(input.userId, input.botId, input.runtime?.env ?? "");
    if (stopping || active.has(input.stepId)) throw new Error("Local step is unavailable");
    // COSEC: a restore must not race another tracked task on the same workspace.
    // Explicit manual cleanup retains its separately confirmed no-active-check policy.
    if (input.stepType !== "runtime_cleanup" && [...active.values()].some((entry) => entry.botId === input.botId
      && entry.stepType !== "runtime_cleanup" && (input.stepType === "restore" || entry.stepType === "restore" || ((input.stepType === "optimize" || entry.stepType === "optimize") && input.taskId !== entry.taskId)))) {
      throw new Error("A local task is still using this Bot; wait before restoring or starting another task");
    }
    const invocation = localInvocation({ ...config, ...target }, input);
    const model = selectedModel(config, input.command);
    mkdirSync(invocation.logDirectory, { recursive: true, mode: 0o700 });
    const fd = openSync(join(invocation.logDirectory, `${invocation.stepId}.log`), "a", 0o600);
    const env: NodeJS.ProcessEnv = { ...process.env, OPENCLAW_STATE_DIR: target.openclawHome,
      OPENCLAW_CONFIG_PATH: join(target.openclawHome, "openclaw.json"), OPENCLAW_HOME: target.openclawHome,
      OPENCLAW_WORKSPACE: target.workspace, SKILL_BASE_DIR: config.skillsRoot,
      CLAWWEB_URL: `http://127.0.0.1:${config.port}`, CLAWWEB_VERSION: "openversion",
      CLAWEVOLVE_PLAN_DISCOVERY_MODEL: model, CLAWEVOLVE_BENCH_MODEL: model, CLAWEVOLVE_OPTIMIZER_MODEL: model,
      CLAWEVOLVE_INVOCATION_CWD: target.workspace, CLAWEVOLVE_CLAWWEB_URL: `http://127.0.0.1:${config.port}` };
    delete env.OPENAI_API_KEY;
    delete env.CLAWBENCH_JUDGE_API_KEY;
    delete env.CLAWBENCH_JUDGE_BASE_URL;
    // Use this Bot's profile token, never a token inherited from another local Bot.
    delete env.OPENCLAW_GATEWAY_TOKEN;
    env.NO_PROXY = [env.NO_PROXY, "localhost,127.0.0.1,::1"].filter(Boolean).join(",");
    env.no_proxy = [env.no_proxy, "localhost,127.0.0.1,::1"].filter(Boolean).join(",");
    let child: ChildProcess;
    try {
      // COSEC: fixed executable + argv; command text is data for Skill argparse, never bash -c/eval.
      child = spawn(invocation.executable, invocation.args, {
        cwd: target.workspace, env, shell: false, detached: true, stdio: ["ignore", fd, fd],
      });
    } finally { closeSync(fd); }
    const done = new Promise<void>((resolveDone) => {
      child.once("close", (code, signal) => {
        void onExit(input, `Local Skill exited (${signal ?? code}); missing terminal Step report. See logs/${input.taskId}/${input.stepId}.log`)
          .catch((error) => { console.error("[clawevolve] Failed to record local exit:", error); })
          .finally(() => { active.delete(input.stepId); resolveDone(); });
      });
    });
    active.set(input.stepId, { child, done, botId: input.botId, taskId: input.taskId, stepType: input.stepType });
    await new Promise<void>((resolveSpawn, reject) => { child.once("spawn", resolveSpawn); child.once("error", reject); });
    return { runId: `local:${input.stepId}`, sessionId: null, platformResponse: {
      message: "Local Skill process started; waiting for Step report",
      evolve_dispatch: { provider: "local", transport: "message" },
    } };
  };
  async function stop(stepId: string) {
    const entry = active.get(stepId);
    if (!entry?.child.pid || entry.child.exitCode !== null || entry.child.signalCode !== null) return;
    const pid = entry.child.pid;
    // COSEC: inspect parentage, never select processes by name/RSS. Record start time
    // so a recycled PID cannot become a cancellation target. Includes setsid children.
    const snapshot = () => execFileSync("ps", ["-axo", "pid=,ppid=,lstart="], { encoding: "utf8", timeout: 5000 })
      .trim().split("\n").flatMap((line) => {
        const match = /^\s*(\d+)\s+(\d+)\s+(.+)$/.exec(line);
        return match ? [{ pid: Number(match[1]), parent: Number(match[2]), started: match[3] }] : [];
      });
    const rows = snapshot();
    const owned = new Set([pid]);
    let changed = true;
    while (changed) {
      changed = false;
      for (const row of rows) if (owned.has(row.parent) && !owned.has(row.pid)) { owned.add(row.pid); changed = true; }
    }
    const targets = rows.filter((row) => owned.has(row.pid));
    const signalTargets = (signal: NodeJS.Signals) => {
      const current = new Map(snapshot().map((row) => [row.pid, row.started]));
      for (const row of [...targets].reverse()) {
        if (current.get(row.pid) !== row.started) continue;
        try { process.kill(row.pid, signal); }
        catch (error) { if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error; }
      }
    };
    signalTargets("SIGTERM");
    // Wait even if the root exits first: a detached child may ignore SIGTERM.
    await new Promise((resolve) => setTimeout(resolve, 300));
    signalTargets("SIGKILL");
    await entry.done;
  }
  const cancel: typeof cancelEvolveExecution = async (input) => {
    const entry = active.get(input.stepId);
    if (input.userId !== config.userId || (entry && (entry.botId !== input.botId || entry.taskId !== input.taskId))) throw new Error("Local Bot binding mismatch");
    await stop(input.stepId);
    return { transport: "local_bash" };
  };
  return { dispatch, cancel, async stop() {
    stopping = true;
    await Promise.all([...active.keys()].map(stop));
  } };
}
