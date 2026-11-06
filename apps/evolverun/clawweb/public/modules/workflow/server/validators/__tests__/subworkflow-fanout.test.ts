// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import type { Request, Response, Router } from "express";
import { validateSpec, validateYaml } from "../workflow-validator.js";
import { createWorkflowsRouter } from "../../routes/workflows.js";

const fanout = () => ({ schemaVersion: "fanout.v1", items: [{ tenant: "a", id: "1", data: { n: 1 } }, { tenant: "b", id: "1" }],
  itemIdFrom: ["tenant", "id"], concurrency: 3, successPolicy: "all_done", finishMode: "wait_all",
  resultPolicy: "collect_all", groupTimeoutSeconds: 10.5, cancelGraceSeconds: 2 });
const executor = () => ({ type: "subworkflow", workflowId: "child", params: { arg: "{{item.id}}" },
  onFailure: "fail", fanout: fanout() });
const spec = (exec: Record<string, unknown> = executor()) => ({ id: "parent", title: "Parent", extra: { preserved: true },
  nodes: [{ id: "child", title: "Child", executor: exec, extraNode: true }] });

function request(router: Router, url: string, body: unknown): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const req = { method: "POST", url, headers: {}, body, get: () => undefined, header: () => undefined } as unknown as Request;
    let status = 200;
    const res = { status(code: number) { status = code; return this; },
      json(data: unknown) { resolve({ status, body: data }); return this; } } as Response;
    router(req, res, (error?: unknown) => error ? reject(error) : resolve({ status: 404, body: {} }));
  });
}

describe("ClawWeb fanout validation", () => {
  it("preserves all fields, without requiring a fixed version", () => {
    const raw = spec();
    const result = validateSpec(raw);
    expect(result.valid).toBe(true);
    expect(result.normalizedSpec).toEqual({ ...raw, version: "1" });
    for (const version of ["inherit", 1, 5]) {
      expect(validateSpec(spec({ ...executor(), version })).valid).toBe(true);
    }
  });
  it.each(["{{params.items}}", " {{nodes.source.output[0]}} ", "{{params.items | default: []}}", []])("accepts items %j", items => {
    expect(validateSpec(spec({ ...executor(), fanout: { ...fanout(), items } })).valid).toBe(true);
  });
  it("accepts old non-fanout subworkflows unchanged", () => {
    const legacy = { type: "subworkflow", workflowId: "child", version: "latest", onFailure: "retry", arbitrary: 1 };
    expect(validateSpec(spec(legacy)).normalizedSpec?.nodes[0].executor).toEqual(legacy);
  });
  it.each([
    ["fanout object", null], ["unknown field", { ...fanout(), typo: true }],
    ["schemaVersion", { ...fanout(), schemaVersion: "fanout.v2" }],
    ["successPolicy", { ...fanout(), successPolicy: "any" }], ["finishMode", { ...fanout(), finishMode: "early" }],
    ["resultPolicy", { ...fanout(), resultPolicy: "first" }], ["missing items", { ...fanout(), items: undefined }],
    ["item expression", { ...fanout(), items: "{{params.x || []}}" }], ["mixed items", { ...fanout(), items: [{ id: "1" }, 2] }],
    ["missing ids", { ...fanout(), itemIdFrom: [] }], ["blank id key", { ...fanout(), itemIdFrom: [" "] }],
    ["duplicate id keys", { ...fanout(), itemIdFrom: ["id", "id"] }],
    ["missing identity", { ...fanout(), items: [{ tenant: "a" }] }],
    ["blank identity", { ...fanout(), items: [{ tenant: "a", id: " " }] }],
    ["duplicate identity", { ...fanout(), items: [{ tenant: "a", id: "1" }, { tenant: "a", id: "1" }] }],
    ["non-json value", { ...fanout(), items: [{ tenant: "a", id: "1", value: NaN }] }],
    ["zero concurrency", { ...fanout(), concurrency: 0 }], ["fractional concurrency", { ...fanout(), concurrency: 1.5 }],
    ["unsafe concurrency", { ...fanout(), concurrency: Number.MAX_SAFE_INTEGER + 1 }],
    ["group timeout", { ...fanout(), groupTimeoutSeconds: Infinity }], ["grace timeout", { ...fanout(), cancelGraceSeconds: 0 }],
  ])("rejects invalid %s", (_name, invalid) => {
    const result = validateSpec(spec({ ...executor(), fanout: invalid }));
    expect(result.valid).toBe(false);
    expect(result.normalizedSpec).toBeNull();
    expect(result.issues[0].message).toContain("fanout");
  });
  it.each(["retry", "skip", null])("rejects fanout onFailure %j", onFailure => {
    expect(validateSpec(spec({ ...executor(), onFailure })).valid).toBe(false);
  });
  it("validates nested loop bodies and node templates without dropping their fields", () => {
    const inner = { id: "nested", executor: executor(), custom: 1 };
    const raw = { ...spec({ type: "loop-group", body: [inner] }), nodeTemplates: { template: { body: [inner], custom: true } } };
    const result = validateSpec(raw);
    expect(result.valid).toBe(true);
    expect(result.normalizedSpec?.nodes[0].executor).toEqual(raw.nodes[0].executor);
    expect(result.normalizedSpec?.nodeTemplates).toEqual(raw.nodeTemplates);
    const bad = { ...inner, executor: { ...executor(), fanout: { ...fanout(), concurrency: 0 } } };
    expect(validateSpec({ ...raw, nodes: [{ ...raw.nodes[0], executor: { type: "loop-group", body: [bad] } }] }).issues[0].message).toContain(".body[nested]");
    expect(validateSpec({ ...raw, nodeTemplates: { template: { body: [bad] } } }).issues[0].message).toContain("nodeTemplates.template.body[nested]");
  });
  it("rejects invalid YAML fanout", () => {
    expect(validateYaml('id: test\nnodes:\n  - id: n\n    executor:\n      type: subworkflow\n      fanout: {}\n').valid).toBe(false);
  });
  it("uses the real validate and save routes and persists the complete fanout", async () => {
    const upsert = vi.fn(async (_id, _pack, specJson) => ({ spec_json: specJson }));
    const repo = { existsByWorkflowId: vi.fn().mockResolvedValue(false), upsert };
    const router = createWorkflowsRouter(repo as never, null, null, null, null);
    const { cancelGraceSeconds: _grace, ...withoutGrace } = fanout();
    const raw = spec({ ...executor(), version: "inherit", fanout: { ...withoutGrace, items: "{{params.items | default: []}}" } });
    const validation = await request(router, "/validate", { spec: raw });
    expect(validation.status).toBe(200);
    expect(validation.body.valid).toBe(true);
    const saved = await request(router, "/save", { workflowId: "parent", botOwnerId: "owner", spec: raw });
    expect(saved.status).toBe(200);
    expect(saved.body.nodes[0].executor).toEqual(raw.nodes[0].executor);
    expect(JSON.parse(upsert.mock.calls[0][2]).nodes[0].executor).toEqual(raw.nodes[0].executor);
    const bad = spec({ ...executor(), fanout: { ...fanout(), concurrency: 0 } });
    expect((await request(router, "/validate", { spec: bad })).body.valid).toBe(false);
    expect((await request(router, "/save", { workflowId: "parent", botOwnerId: "owner", spec: bad })).status).toBe(400);
    expect(upsert).toHaveBeenCalledTimes(1);
  });
});

// Set CLAWMIND_WORKFLOW_VALIDATOR to the engine's src/validation/workflow.ts to
// run this same portable fixture matrix against its actual exported schema.
describe("ClawMind fanout schema contract", () => {
  it("agrees on executor and fanout shape, including boundaries", async () => {
    const now = 1_700_000_000_000;
    const clock = vi.spyOn(Date, "now").mockReturnValue(now);
    try {
      const enginePath = process.env.CLAWMIND_WORKFLOW_VALIDATOR;
      const engine = enginePath ? await import(/* @vite-ignore */ enginePath) : null;
      if (enginePath) expect(typeof engine.validateFanoutExecutorSchema).toBe("function");
      const good = executor();
      const cases: Array<[string, unknown, boolean]> = [];
      const add = (name: string, patch: Record<string, unknown>, valid: boolean) => cases.push([name, { ...good, ...patch }, valid]);
      const addFanout = (name: string, patch: Record<string, unknown>, valid: boolean) => add(name, { fanout: { ...fanout(), ...patch } }, valid);
      add("baseline", {}, true);
      class Executor { type = good.type; workflowId = good.workflowId; fanout = good.fanout; }
      cases.push(["non-plain executor", new Executor(), false]);
      add("non-plain params", { params: new (class { value = "ok"; })() }, false);
      add("explicit inherit", { version: "inherit" }, true);
      add("explicit positive version", { version: Number.MAX_SAFE_INTEGER }, true);
      for (const value of ["pinned", "latest", "1", 0, -1, 1.1, Number.MAX_SAFE_INTEGER + 1, null]) add(`invalid version ${value}`, { version: value }, false);
      for (const [name, patch] of [
        ["unknown executor key", { custom: 1 }], ["concurrent", { concurrent: false }], ["sessionKey", { sessionKey: "s" }],
        ["missing workflowId", { workflowId: undefined }], ["blank workflowId", { workflowId: "  " }],
        ["blank packId", { packId: " " }], ["null packId", { packId: null }], ["null params", { params: null }],
        ["array params", { params: [] }], ["non-string param", { params: { a: 1 } }], ["null onFailure", { onFailure: null }],
        ["undefined fanout property", { fanout: undefined }],
      ] as Array<[string, Record<string, unknown>]>) add(name, patch, false);
      add("packId and params", { packId: "pack", params: { a: "", b: "{{item.id}}" } }, true);
      addFanout("optional grace", { cancelGraceSeconds: undefined }, true);
      for (const [field, value] of [
        ["schemaVersion", undefined], ["schemaVersion", "fanout.v2"],
        ["successPolicy", undefined], ["successPolicy", "any"], ["finishMode", "early"],
        ["resultPolicy", "first"], ["concurrency", 0], ["concurrency", 1.5],
        ["concurrency", Number.MAX_SAFE_INTEGER + 1], ["groupTimeoutSeconds", undefined],
        ["groupTimeoutSeconds", 0], ["groupTimeoutSeconds", Infinity], ["cancelGraceSeconds", 0],
        ["cancelGraceSeconds", NaN], ["itemIdFrom", []], ["itemIdFrom", [" "]],
        ["items", undefined], ["items", {}], ["items", [1]],
        ["items", [{ tenant: "a" }]], ["items", [{ tenant: "a", id: " " }]],
        ["items", [{ tenant: "a", id: "b" }, { tenant: "a", id: "b" }]],
      ] as Array<[string, unknown]>) addFanout(`invalid ${field}: ${String(value)}`, { [field]: value }, false);
      add("undefined fanout", { fanout: undefined }, false);
      add("null fanout", { fanout: null }, false);
      add("array fanout", { fanout: [] }, false);
      add("retry failure", { onFailure: "retry" }, false);
      add("skip failure", { onFailure: "skip" }, false);
      add("omitted params", { params: undefined }, true);
      addFanout("empty items", { items: [] }, true);
      addFanout("template default", { items: " {{params.items | default: []}} " }, true);
      addFanout("template literal default", { items: '{{nodes.result | default: "hello"}}' }, true);
      addFanout("interpolated template", { items: "prefix {{params.items}}" }, false);
      addFanout("empty default", { items: "{{params.items | default:}}" }, false);
      addFanout("distinct untrimmed keys", { itemIdFrom: ["id", " id "], items: [] }, true);
      addFanout("duplicate keys", { itemIdFrom: ["id", "id"] }, false);
      addFanout("unknown field", { extra: 1 }, false);
      addFanout("deadline upper bound", { groupTimeoutSeconds: (8.64e15 - now) / 1000 }, true);
      addFanout("deadline overflow", { groupTimeoutSeconds: (8.64e15 - now) / 1000 + 1 }, false);
      addFanout("grace overflow", { cancelGraceSeconds: (8.64e15 - now) / 1000 + 1 }, false);
      addFanout("undefined nested property", { items: [{ tenant: "a", id: "b", value: undefined }] }, false);
      addFanout("invalid unicode identity", { items: [{ tenant: "a", id: "\ud800" }] }, false);
      addFanout("slash-safe identity", { items: [{ tenant: "a/b", id: "c" }, { tenant: "a", id: "b/c" }] }, true);
      addFanout("sparse items", { items: Array(1) }, false);
      addFanout("sparse nested array", { items: [{ tenant: "a", id: "b", value: Array(1) }] }, false);
      const cycle: Record<string, unknown> = {}; cycle.self = cycle;
      addFanout("cycle", { items: [{ tenant: "a", id: "b", value: cycle }] }, false);
      addFanout("non-json class", { items: [{ tenant: "a", id: "b", value: new Date() }] }, false);
      for (const [name, input, expected] of cases) {
        const web = validateSpec(spec(input as Record<string, unknown>));
        expect(web.valid, `ClawWeb: ${name}`).toBe(expected);
        if (web.valid) expect(web.normalizedSpec?.nodes[0].executor, name).toEqual(input);
        if (engine) {
          let accepted = true;
          try { engine.validateFanoutExecutorSchema(input); } catch { accepted = false; }
          expect(accepted, `ClawMind: ${name}`).toBe(expected);
          expect(web.valid, `cross-end: ${name}`).toBe(accepted);
        }
      }
    } finally { clock.mockRestore(); }
  });
});

describe("nested fanout workflow contract", () => {
  const child = (concurrency: number) => ({ id: "fan", title: "Fan", phase: "P1",
    executor: { ...executor(), fanout: { ...fanout(), concurrency } } });
  const legacy = (concurrency: number, includeExecutor = false) => ({ id: "old-loop", title: "Old loop", phase: "P1",
    type: "loop-group", maxIterations: 2, body: [child(concurrency)],
    ...(includeExecutor ? { executor: { type: "done" } } : {}) });
  const workflow = (nodes: unknown[], extra: Record<string, unknown> = {}) => ({ id: "wf", title: "Workflow", nodes, ...extra });
  const goal = (inner: unknown, planType = "spec") => ({ id: "goal", title: "Goal", phase: "P1", executor: {
    type: "goal-loop", goal: "Finish", evaluation: { criteria: ["done"] }, initialPlan: { type: planType, spec: inner },
  } });
  const done = { id: "done", title: "Done", phase: "P1", executor: { type: "done" } };
  const fixtures: Array<[string, (n: number) => ReturnType<typeof workflow>, string]> = [
    ["goal-loop initial plan", n => workflow([goal(workflow([child(n)]))]), "initialPlan.spec.nodes[fan]"],
    ["legacy top-level loop", n => workflow([legacy(n, true)]), "nodes[old-loop].body[fan]"],
    ["legacy loop without executor in goal plan", n => workflow([goal(workflow([legacy(n)]))]), "nodes[old-loop].body[fan]"],
    ["goal plan template", n => workflow([goal(workflow([done], { nodeTemplates: { t: { body: [child(n)] } } }))]), "initialPlan.spec.nodeTemplates.t.body[fan]"],
    ["legacy loop in template", n => workflow([done], { nodeTemplates: { t: { body: [legacy(n)] } } }), "nodeTemplates.t.body[old-loop].body[fan]"],
    ["goal in executor loop", n => workflow([{ id: "loop", title: "Loop", phase: "P1", executor: {
      type: "loop-group", maxIterations: 2, body: [goal(workflow([child(n)]))],
    } }]), "initialPlan.spec.nodes[fan]"],
    ["nested goal plans", n => workflow([goal(workflow([goal(workflow([child(n)]))]))]), "initialPlan.spec.nodes[fan]"],
  ];
  it.each(fixtures)("validates and preserves %s on both sides", async (_name, make, errorPath) => {
    const enginePath = process.env.CLAWMIND_WORKFLOW_VALIDATOR;
    const engine = enginePath ? await import(/* @vite-ignore */ enginePath) : null;
    if (enginePath) expect(typeof engine.normalizeWorkflowSpec).toBe("function");
    const valid = make(2);
    const invalid = make(0);
    const accepted = validateSpec(valid);
    expect(accepted.valid).toBe(true);
    expect(accepted.normalizedSpec?.nodes).toEqual(valid.nodes);
    expect(accepted.normalizedSpec?.nodeTemplates).toEqual((valid as Record<string, unknown>).nodeTemplates);
    const rejected = validateSpec(invalid);
    expect(rejected.valid).toBe(false);
    expect(rejected.normalizedSpec).toBeNull();
    expect(rejected.issues[0].message).toContain(errorPath);
    expect(rejected.issues[0].message).toContain("fanout.concurrency");
    if (engine) {
      expect(() => engine.normalizeWorkflowSpec(valid)).not.toThrow();
      expect(() => engine.normalizeWorkflowSpec(invalid)).toThrow(/fanout\.concurrency/);
    }
  });
  it.each(["synthesize", "omitted"])("ignores invalid spec for %s initial plans on both sides", async planType => {
    const inner = workflow([child(0)]);
    const node = goal(inner, "synthesize");
    if (planType === "omitted") delete (node.executor.initialPlan as { type?: string }).type;
    const raw = workflow([node]);
    const web = validateSpec(raw);
    expect(web.valid).toBe(true);
    expect(web.normalizedSpec?.nodes[0].executor).toEqual(node.executor);
    const router = createWorkflowsRouter(null, null, null, null, null);
    const response = await request(router, "/validate", { spec: raw });
    expect(response.status).toBe(200);
    expect(response.body.valid).toBe(true);
    expect(response.body.normalizedSpec.nodes[0].executor).toEqual(node.executor);

    // The exact same invalid inner workflow must be checked when type is spec.
    const specified = workflow([goal(inner, "spec")]);
    expect(validateSpec(specified).valid).toBe(false);
    const specifiedResponse = await request(router, "/validate", { spec: specified });
    expect(specifiedResponse.body.valid).toBe(false);
    expect(specifiedResponse.body.issues[0].message).toContain("fanout.concurrency");

    const enginePath = process.env.CLAWMIND_WORKFLOW_VALIDATOR;
    if (enginePath) {
      const engine = await import(/* @vite-ignore */ enginePath);
      const normalized = engine.normalizeWorkflowSpec(raw);
      expect(normalized.nodes[0].executor.initialPlan).toMatchObject({ type: "synthesize", spec: inner });
      expect(() => engine.normalizeWorkflowSpec(specified)).toThrow(/fanout\.concurrency/);
    }
  });
  it("gives the modern executor loop precedence over legacy node.body", async () => {
    const raw = workflow([{ ...legacy(0), executor: { type: "loop-group", maxIterations: 2, body: [child(2)] } }]);
    expect(validateSpec(raw).valid).toBe(true);
    const enginePath = process.env.CLAWMIND_WORKFLOW_VALIDATOR;
    if (enginePath) {
      const engine = await import(/* @vite-ignore */ enginePath);
      expect(() => engine.normalizeWorkflowSpec(raw)).not.toThrow();
    }
  });
});
