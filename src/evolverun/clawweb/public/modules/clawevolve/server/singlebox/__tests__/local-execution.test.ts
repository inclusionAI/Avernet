import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createLocalExecution, localInvocation } from "../local-execution.js";
import type { LocalBotPaths } from "../bot-runtime.js";
import type { SingleboxConfig } from "../config.js";
import type { EvolveDispatchInput } from "../../services/evolve-dispatcher.js";

const dirs: string[] = [];
const runners: ReturnType<typeof createLocalExecution>[] = [];
function fixture(script = 'printf "%s\\n" "$@"\n') {
  const root = mkdtempSync(join(tmpdir(), "ce-local-")); dirs.push(root);
  for (const stage of ["diagnose", "plan"]) {
    mkdirSync(join(root, `clawevolve-${stage}/scripts`), { recursive: true });
    writeFileSync(join(root, `clawevolve-${stage}/scripts/run.sh`), script);
  }
  const config: SingleboxConfig & LocalBotPaths = { userId: "owner", botId: "bot", botEnv: "dev", model: "local/model",
    backendDb: join(root, "backend.db"), skillsRoot: root, botsRoot: root, openclawHome: root,
    workspace: root, dataDirectory: root, port: 5173 };
  const input = { userId: "owner", botId: "bot", taskId: "EV-test", stepId: "STEP-test", stepPk: 1,
    stepType: "diagnose", command: "/clawevolve-diagnose --intent 'a b'", mode: "message",
    runtime: { env: "dev" } } as EvolveDispatchInput;
  return { config, input, root };
}
afterEach(async () => { await Promise.all(runners.splice(0).map((runner) => runner.stop())); for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true }); });

describe("Singlebox fixed Bash invocation", () => {
  it("passes command text as one argument, never evaluates shell interpolation", async () => {
    const { config, input, root } = fixture();
    input.command = `/clawevolve-diagnose --intent '$(touch ${root}/injected); echo bad'`;
    const exit = vi.fn(async () => {});
    const runner = createLocalExecution(config, exit, async () => config); runners.push(runner);
    expect((await runner.dispatch(input)).runId).toBe("local:STEP-test");
    await vi.waitFor(() => expect(exit).toHaveBeenCalledOnce());
    expect(readFileSync(join(root, "logs/EV-test/STEP-test.log"), "utf8")).toContain(input.command);
    expect(() => readFileSync(join(root, "injected"))).toThrow();
  });
  it("pins profile and output paths and supports only the two approved stages", () => {
    const { config, input, root } = fixture();
    expect(localInvocation(config, input).args).toContain(join(root, "clawevolve_results/EV-test/diagnose/output"));
    const plan = localInvocation(config, { ...input, stepType: "plan", command: "/clawevolve-plan --goal test" });
    expect(plan.args).toContain("--evolve-results-dir");
    for (const patch of [{ stepType: "optimize" }, { userId: "other" }, { taskId: "../x" },
      { command: "/clawevolve-diagnose --openclaw-home /other" }]) {
      expect(() => localInvocation(config, { ...input, ...patch })).toThrow();
    }
  });
  it("rejects a script symlink outside the configured checkout", () => {
    const { config, input, root } = fixture();
    const outside = mkdtempSync(join(tmpdir(), "ce-outside-")); dirs.push(outside);
    writeFileSync(join(outside, "run.sh"), "true");
    const script = join(root, "clawevolve-diagnose/scripts/run.sh"); rmSync(script); symlinkSync(join(outside, "run.sh"), script);
    expect(() => localInvocation(config, input)).toThrow("escapes");
  });
  it("cancels only a process started by this Singlebox instance", async () => {
    const { config, input } = fixture("sleep 30\n");
    const exit = vi.fn(async () => {});
    const runner = createLocalExecution(config, exit, async () => config); runners.push(runner);
    await runner.dispatch(input);
    expect(await runner.cancel({ ...input, sessionId: null, platformResponse: null })).toEqual({ transport: "local_bash" });
    expect(exit).toHaveBeenCalledOnce();
  });
});

it("pins manual cleanup to openversion and the selected Bot, without the internal launcher", () => {
  const { config, input, root } = fixture();
  mkdirSync(join(root, "scripts"));
  writeFileSync(join(root, "scripts/clawevolve_runtime_cleanup.py"), "pass\n");
  const invocation = localInvocation(config, { ...input, stepType: "runtime_cleanup",
    command: "/clawevolve-runtime-cleanup --task-id EV-test --step-id STEP-test --version internalversion" });
  expect(invocation.executable).toBe("python3");
  expect(invocation.args).toContain("openversion");
  expect(invocation.args).not.toContain("internalversion");
  expect(invocation.args).toContain("--force-cleanup");
  expect(invocation.args.slice(invocation.args.indexOf("--openclaw-home"), invocation.args.indexOf("--openclaw-home") + 2)).toEqual(["--openclaw-home", config.openclawHome]);
  expect(invocation.args.join(" ")).not.toMatch(/supervisorctl|task_launcher|async_runner/);
});


it("pins Bench identity, callback and model while forwarding frozen template options", () => {
  const { config, input, root } = fixture();
  mkdirSync(join(root, "clawevolve-workflow/scripts/handlers"), { recursive: true });
  writeFileSync(join(root, "clawevolve-workflow/scripts/handlers/clawevolve_bench_run.py"), "pass\n");
  const bench = { ...input, stepType: "bench", command: "/clawevolve-bench --domain-id own-domain --template-name sample --template-version 2 --suite all --scene claw-evolve-bench --model other/model" };
  const result = localInvocation(config, bench);
  expect(result.executable).toBe("python3");
  const option = (name: string) => result.args[result.args.indexOf(name) + 1];
  expect(option("--domain-id")).toBe("own-domain");
  expect(option("--template-name")).toBe("sample");
  expect(option("--template-version")).toBe("2");
  expect(option("--owner-id")).toBe(config.userId);
  expect(option("--model")).toBe("other/model");
  expect(option("--judge")).toBe("other/model");
  expect(option("--clawweb-url")).toBe("http://127.0.0.1:5173");
  expect(() => localInvocation(config, { ...bench, command: "/clawevolve-bench --suite all" })).toThrow("Domain");
});

it("uses the existing Pack handler with fixed mode and local callback", () => {
  const { config, input, root } = fixture();
  mkdirSync(join(root, "clawevolve-workflow/scripts/handlers"), { recursive: true });
  writeFileSync(join(root, "clawevolve-workflow/scripts/handlers/clawevolve_pack_run.py"), "pass\n");
  const result = localInvocation(config, { ...input, stepType: "pack", command: "/clawevolve-pack --mode pack" });
  expect(result.executable).toBe("python3");
  expect(result.args.slice(3)).toEqual(["--mode", "pack", "--task-id", "EV-test", "--step-id", "STEP-test", "--clawweb-url", "http://127.0.0.1:5173"]);
});

it("pins restore namespace and verifies frozen source options", () => {
  const { config, input, root } = fixture();
  mkdirSync(join(root, "clawevolve-workflow/scripts/handlers"), { recursive: true });
  writeFileSync(join(root, "clawevolve-workflow/scripts/handlers/clawevolve_pack_run.py"), "pass\n");
  for (const kind of ["snapshot", "baseline", "round"]) {
    const result = localInvocation(config, { ...input, stepType: "restore", command: `/clawevolve-pack --mode restore --source-task-id EV-source --source-kind ${kind} --source-round 1` });
    expect(result.args).toContain("openversion");
    expect(result.args).toContain("clawevolve-artifacts");
    expect(result.args).toContain(kind);
  }
  expect(() => localInvocation(config, { ...input, stepType: "restore", command: "/clawevolve-pack --source-task-id ../x --source-kind snapshot" })).toThrow();
  expect(() => localInvocation(config, { ...input, stepType: "restore", command: "/clawevolve-pack --source-task-id EV-source --source-kind round --source-round 0" })).toThrow();
});

it("does not begin a restore while another local process uses the Bot", async () => {
  const { config, input } = fixture("sleep 30\n");
  const runner = createLocalExecution(config, async () => {}, async () => config); runners.push(runner);
  await runner.dispatch(input);
  await expect(runner.dispatch({ ...input, stepId: "STEP-restore", stepType: "restore", command: "/clawevolve-pack --mode restore --source-task-id EV-source --source-kind snapshot" })).rejects.toThrow("still using this Bot");
});

it.each(["optimize", "bench_plan"])("routes %s to its original handler with local environment only", (stage) => {
  const { config, input, root } = fixture();
  mkdirSync(join(root, "clawevolve-workflow/scripts/handlers"), { recursive: true });
  writeFileSync(join(root, `clawevolve-workflow/scripts/handlers/clawevolve_${stage}_run.py`), "pass\n");
  const command = stage === "optimize"
    ? "/clawevolve-workflow --stage optimize --round 2 --train-bench-domain-id TRAIN --test-bench-domain-id TEST"
    : "/clawevolve-workflow --stage bench-plan --train-domain-id TRAIN --test-domain-id TEST";
  const invocation = localInvocation(config, { ...input, stepType: stage, command });
  expect(invocation.executable).toBe("python3");
  expect(invocation.args).toContain(config.workspace);
  expect(invocation.args).toContain(config.skillsRoot);
  expect(invocation.args).toContain(config.model);
  expect(invocation.args).toContain("TRAIN");
  expect(invocation.args).toContain("TEST");
  if (stage === "optimize") {
    expect(invocation.args).toContain("run-round");
    expect(invocation.args).toContain("--tune-model");
    expect(invocation.args).not.toContain("--skip-oss");
    expect(() => localInvocation(config, { ...input, stepType: stage, command: command.replace("--round 2", "--round -1") })).toThrow("round");
  }
});

it("cancels detached descendants without touching unrelated processes", async () => {
  const { config, input, root } = fixture(`python3 -c 'import subprocess,time; p=subprocess.Popen(["python3","-c","import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"], start_new_session=True); print(p.pid,flush=True); time.sleep(30)'\n`);
  const runner = createLocalExecution(config, async () => {}, async () => config); runners.push(runner);
  const unrelated = (await import("node:child_process")).spawn("sleep", ["30"]);
  try {
    await runner.dispatch(input);
    let child = 0;
    await vi.waitFor(() => { child = Number(readFileSync(join(root, "logs/EV-test/STEP-test.log"), "utf8").trim()); expect(child).toBeGreaterThan(0); });
    await runner.cancel({ ...input, sessionId: null, platformResponse: null });
    await vi.waitFor(() => expect(() => process.kill(child, 0)).toThrow());
    expect(() => process.kill(unrelated.pid!, 0)).not.toThrow();
  } finally { unrelated.kill("SIGKILL"); }
});
