import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeDiagnoseIntent,
  normalizeEvolutionGoal,
  quoteCommandArgument,
  readDiagnoseJudgeBackend,
  readNodeCommandOption,
  redactSecret,
  renderCommand,
  resolveDiagnoseJudgeBackend,
  resolveOpenClawExecutionMode,
} from "../src/server/services/evolve/command.js";

test("normalizes goals without changing the existing contract", () => {
  const goal = normalizeEvolutionGoal("  修复  工具失败\n并保留 '$(whoami)'  ");
  assert.equal(goal, "修复 工具失败 并保留 '$(whoami)'");
  assert.equal(quoteCommandArgument(goal), `'修复 工具失败 并保留 '"'"'$(whoami)'"'"''`);
  assert.throws(() => normalizeEvolutionGoal(123), /goal 必须是字符串/);
  assert.throws(() => normalizeEvolutionGoal("a\0b"), /NUL/);
  assert.throws(() => normalizeEvolutionGoal("目".repeat(2001)), /2000/);
});

test("normalizes diagnose intent with the existing limits", () => {
  assert.equal(normalizeDiagnoseIntent("  检查\n工具  失败 "), "检查 工具 失败");
  assert.throws(() => normalizeDiagnoseIntent("  "), /不能为空/);
  assert.throws(() => normalizeDiagnoseIntent("a\0b"), /NUL/);
  assert.throws(() => normalizeDiagnoseIntent("诊".repeat(4001)), /4000/);
});

test("resolves public execution and judge modes", () => {
  assert.equal(resolveOpenClawExecutionMode(undefined), "local");
  assert.equal(resolveOpenClawExecutionMode(" GATEWAY "), "gateway");
  assert.throws(() => resolveOpenClawExecutionMode("remote"), /local 或 gateway/);

  assert.equal(resolveDiagnoseJudgeBackend(undefined, ""), "subagent");
  assert.equal(resolveDiagnoseJudgeBackend(undefined, "secret"), "api");
  assert.equal(resolveDiagnoseJudgeBackend("subagent", "secret"), "subagent");
  assert.throws(() => resolveDiagnoseJudgeBackend("other", ""), /subagent 或 api/);
  assert.equal(readDiagnoseJudgeBackend("/command --judge-backend subagent"), "subagent");
  assert.equal(readDiagnoseJudgeBackend("/command"), "api");
});

test("redacts nested secret values", () => {
  assert.deepEqual(
    redactSecret({ token: "before-secret-after", nested: ["secret", 1] }, "secret"),
    { token: "before-******-after", nested: ["******", 1] },
  );
});

test("reads command options and renders command templates", () => {
  const template = "/clawevolve-workflow --stage optimize --model {{model}}";
  assert.equal(readNodeCommandOption(template.replace("{{model}}", "model-a"), "model"), "model-a");
  assert.equal(
    renderCommand(template, { model: "model-a" }, [["task-id", "EV-1"], ["round", 2]]),
    "/clawevolve-workflow --stage optimize --model model-a --task-id EV-1 --round 2",
  );
  assert.throws(() => renderCommand("/command {{missing}}", {}, []), /未解析变量/);
  assert.throws(
    () => renderCommand("/command {{value}}", { value: "x".repeat(70 * 1024) }, []),
    /64 KiB/,
  );
});
