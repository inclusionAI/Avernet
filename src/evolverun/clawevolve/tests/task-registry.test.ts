import assert from "node:assert/strict";
import test from "node:test";

import { createTaskDefinitionsResponse } from "../src/server/routes/evolve.js";
import {
  createEvolveNodeRegistry,
  EVOLVE_TASK_REGISTRY,
  isEvolveTaskType,
  taskNodeKeys,
} from "../src/server/services/evolve/task-registry.js";

test("publishes the complete Clawevolve task set", () => {
  assert.deepEqual(Object.keys(EVOLVE_TASK_REGISTRY), [
    "diagnose",
    "optimize",
    "apply",
    "full",
    "bench",
    "bench_optimize",
    "pack",
    "pack_restore",
    "runtime_cleanup",
    "repair",
    "suggestion_apply",
    "run_analysis",
  ]);
  assert.equal(isEvolveTaskType("full"), true);
  assert.equal(isEvolveTaskType("repair"), true);
  assert.equal(isEvolveTaskType("run_analysis"), true);
  assert.deepEqual(taskNodeKeys("bench_optimize"), ["bench_plan", "optimize"]);
  assert.deepEqual(taskNodeKeys("full", "insight_improvement"), ["plan", "optimize"]);
});

test("keeps private hosted defaults outside the public registry", () => {
  const registry = createEvolveNodeRegistry();
  assert.equal(registry.bench.defaultCommand, "/clawevolve-bench --model {{model}} --suite all");
  assert.match(registry.optimize.defaultCommand, /--model \{\{model\}\}/);

  const hosted = createEvolveNodeRegistry({ bench: "/clawevolve-bench --model hosted-model --suite all" });
  assert.equal(hosted.bench.defaultCommand, "/clawevolve-bench --model hosted-model --suite all");
});

test("builds task definitions while allowing host-only variants", () => {
  const response = createTaskDefinitionsResponse({ variants: { hosted_variant: ["plan", "optimize"] } });
  assert.deepEqual(
    response.tasks.find((item) => item.type === "bench_optimize")?.nodes.map((node) => node.key),
    ["bench_plan", "optimize"],
  );
  assert.deepEqual(response.variants.hosted_variant.map((node) => node.key), ["plan", "optimize"]);
});

test("allows a host to append deployment-specific task definitions", () => {
  const response = createTaskDefinitionsResponse({
    taskRegistry: {
      ...EVOLVE_TASK_REGISTRY,
      host_only: {
        type: "host_only",
        label: "Host only",
        nodes: [],
      },
    },
  });
  assert.equal(response.tasks.at(-1)?.type, "host_only");
  assert.equal("host_only" in EVOLVE_TASK_REGISTRY, false);
});
