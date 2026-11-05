import assert from "node:assert/strict";
import { test } from "node:test";
import { normalizeWorkflowSpec, validateWorkflowSemantics, WorkflowValidationError } from "../src/validation/workflow.js";

const config = () => ({
  schemaVersion: "fanout.v1", items: "{{nodeOutput.plan.items}}", itemIdFrom: ["scene_code", "partition"],
  concurrency: 2, successPolicy: "all_done", finishMode: "wait_all", resultPolicy: "collect_all",
  groupTimeoutSeconds: 1800, cancelGraceSeconds: 30,
});
const workflow = (executor: Record<string, unknown>) => ({
  id: "parent", title: "Parent", nodes: [{ id: "fan", title: "Fanout", phase: "P5", executor: { type: "subworkflow", workflowId: "child", ...executor } }],
});
function rejects(executor: Record<string, unknown>, field: string) {
  assert.throws(() => normalizeWorkflowSpec(workflow(executor)), (error: unknown) =>
    error instanceof WorkflowValidationError && error.issues.some(issue => issue.path.includes(field)));
}

test("fanout.v1 keeps its fixed policies and a single template reference", () => {
  const spec = normalizeWorkflowSpec(workflow({ version: "pinned", fanout: config() }));
  assert.deepEqual(spec.nodes[0].executor.fanout, config());
  validateWorkflowSemantics(spec); // Authored drafts need not have a release lock yet.
});

test("rejects fanout without pinned rather than silently keeping unknown config", () => {
  rejects({ fanout: config() }, "version");
  rejects({ version: "latest", fanout: config() }, "version");
});

for (const [field, values] of Object.entries({
  schemaVersion: [undefined, "fanout.v2"], items: [undefined, [null], [1], [[]], "plain", "{{params.a}} {{params.b}}", "prefix {{params.a}}", "{{params.a | default: 1}}", "{{}}"],
  itemIdFrom: [undefined, [], [""], [" "], ["id", "id"], [1]],
  concurrency: [undefined, 0, -1, 1.5, "2", Infinity, Number.MAX_SAFE_INTEGER + 1],
  successPolicy: [undefined, "any_success", "all_success"], finishMode: [undefined, "cancel_remaining"],
  resultPolicy: [undefined, "first"], groupTimeoutSeconds: [undefined, 0, -1, Infinity, NaN, "1800"],
  cancelGraceSeconds: [undefined, 0, -1, Infinity, NaN, "30"],
})) {
  for (const value of values) test(`rejects invalid ${field}=${JSON.stringify(value)}`, () => {
    rejects({ version: "pinned", fanout: { ...config(), [field]: value } }, field);
  });
}
for (const key of ["count", "any_success", "cancel_remaining", "typo"]) {
  test(`rejects unknown fanout key ${key} before deep-preserve can restore it`, () => {
    rejects({ version: "pinned", fanout: { ...config(), [key]: 1 } }, key);
  });
}
for (const fanout of [null, "items", []]) test(`rejects non-object fanout ${JSON.stringify(fanout)}`, () => {
  rejects({ version: "pinned", fanout }, "fanout");
});

test("legacy single subworkflow keeps params and metadata without requiring a lock", () => {
  const spec = normalizeWorkflowSpec(workflow({ params: { value: "{{params.value}}" }, extension: { old: true } }));
  assert.equal(spec.nodes[0].executor.version, undefined);
  assert.deepEqual(spec.nodes[0].executor.extension, { old: true });
  validateWorkflowSemantics(spec);
});

test("semantic validation also rejects invalid fanout supplied without normalization", () => {
  const spec = normalizeWorkflowSpec(workflow({ version: "pinned", fanout: config() }));
  (spec.nodes[0].executor.fanout as Record<string, unknown>).count = 5;
  assert.throws(() => validateWorkflowSemantics(spec), /count/);
});

test("fanout validation covers loop bodies and reusable node templates", () => {
  for (const location of ["loop", "template"]) {
    const child = { id: "fan", title: "Fanout", phase: "P5", executor: { type: "subworkflow", workflowId: "child", version: "pinned", fanout: { ...config(), count: 5 } } };
    const raw = location === "loop"
      ? { id: "parent", title: "Parent", nodes: [{ id: "loop", title: "Loop", phase: "P5", executor: { type: "loop-group", maxIterations: 2, iterationVar: "iteration", body: [child] } }] }
      : { id: "parent", title: "Parent", nodes: [], nodeTemplates: { expand: { body: [child] } } };
    assert.throws(() => normalizeWorkflowSpec(raw), /count/);
  }
});

test("malformed explicitly supplied dependency locks are not passed through as metadata", () => {
  for (const dependencies of [null, [], { child: { workflowId: "child" } }]) {
    assert.throws(() => normalizeWorkflowSpec({ ...workflow({}), subworkflowDependencies: dependencies }), /DEPENDENCY_LOCK_INVALID/);
  }
});

for (const items of [[], [{ scene_code: "scene", partition: "p1" }]]) {
  test(`accepts static object-array items ${JSON.stringify(items)}`, () => {
    const spec = normalizeWorkflowSpec(workflow({ version: "pinned", fanout: { ...config(), items } }));
    assert.deepEqual(spec.nodes[0].executor.fanout.items, items);
  });
}

for (const items of [[{}], [{ scene_code: "scene", partition: 1 }], [
  { scene_code: "scene", partition: "p1" }, { scene_code: "scene", partition: "p1" },
], [{ scene_code: "scene", partition: "p1", value: Infinity }]]) {
  test(`rejects statically invalid item identities/content ${JSON.stringify(items)}`, () => {
    rejects({ version: "pinned", fanout: { ...config(), items } }, "items");
  });
}
for (const onFailure of ["retry", "skip"]) test(`fanout refuses unsupported onFailure=${onFailure}`, () => {
  rejects({ version: "pinned", fanout: config(), onFailure }, "onFailure");
});

test("finite positive fractional seconds match the runtime duration contract", () => {
  const fanout = { ...config(), groupTimeoutSeconds: 0.5, cancelGraceSeconds: 0.03 };
  const spec = normalizeWorkflowSpec(workflow({ version: "pinned", fanout }));
  assert.deepEqual(spec.nodes[0].executor.fanout, fanout);
  validateWorkflowSemantics(spec);
});
