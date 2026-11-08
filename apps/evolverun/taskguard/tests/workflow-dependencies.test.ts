import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash } from "node:crypto";
import type { WorkflowSpec, PinnedWorkflowDependency } from "../src/types.js";
import type { DeployHistoryDetailRow } from "../src/db/repositories/types.js";
import * as locks from "../src/workflow-dependencies.js";
import { freezeSubworkflowDependencies, dependencyFromPublishedHistory, assertDependencySnapshotPreserved } from "../src/controller/subworkflow-dependencies.js";
import { normalizeWorkflowSpec } from "../src/validation/workflow.js";

const leaf = (id = "child"): WorkflowSpec => ({ id, title: id, nodes: [] });
const call = (workflowId: string, version?: "pinned") => ({ id: workflowId, title: workflowId, phase: "P1", dependsOn: [], executor: { type: "subworkflow" as const, workflowId, ...(version ? { version } : {}) } });
const parent = (): WorkflowSpec => ({ ...leaf("parent"), nodes: [call("child", "pinned")] });
function pin(spec = leaf(), publishedVersion = 1): PinnedWorkflowDependency {
  return { workflowId: spec.id, publishedVersion, releaseId: `deploy/${spec.id}/#${publishedVersion}`, digest: locks.digestWorkflowSpec(spec), spec };
}
const release = (spec = leaf(), overrides = {}): DeployHistoryDetailRow => ({
  deployNumber: 9, version: 3, tagName: `deploy/${spec.id}/#9`, action: "deploy", specJson: JSON.stringify(spec), gmtCreate: 1, ...overrides,
});

test("canonical SHA256 sorts nested object keys but preserves arrays and all published content", () => {
  const a = { ...leaf(), config: { z: [2, 1], a: { y: 2, x: 1 } } };
  const b = { nodes: [], config: { a: { x: 1, y: 2 }, z: [2, 1] }, title: "child", id: "child" };
  const canonical = '{"config":{"a":{"x":1,"y":2},"z":[2,1]},"id":"child","nodes":[],"title":"child"}';
  assert.equal(locks.canonicalWorkflowJson(a), canonical);
  assert.equal(locks.digestWorkflowSpec(a), `sha256:${createHash("sha256").update(canonical).digest("hex")}`);
  assert.equal(locks.digestWorkflowSpec(a), locks.digestWorkflowSpec(b));
  assert.notEqual(locks.digestWorkflowSpec(a), locks.digestWorkflowSpec({ ...b, config: { ...b.config, z: [1, 2] } }));
});

test("canonicalization permits normalized optional object fields, rejects non-JSON values and object cycles", () => {
  assert.equal(locks.canonicalWorkflowJson({ a: undefined, b: null }), '{"b":null}');
  for (const invalid of [NaN, Infinity, 1n, () => 1, new Date(), [undefined], Array(1)]) {
    assert.throws(() => locks.canonicalWorkflowJson(invalid), /DEPENDENCY_LOCK_INVALID/);
  }
  const cycle: Record<string, unknown> = {}; cycle.self = cycle;
  assert.throws(() => locks.canonicalWorkflowJson(cycle), /DEPENDENCY_LOCK_INVALID/);
});

test("pinned lookup uses the release snapshot, never saved/current specs, and returns a detached copy", () => {
  const spec = parent(); spec.version = 7; spec.subworkflowDependencies = { child: pin() };
  const snapshot = locks.snapshotWorkflowDependencies(spec)!;
  spec.subworkflowDependencies.child.spec.title = "saved v2";
  const child = locks.resolvePinnedWorkflowDependency(snapshot, "child");
  assert.equal(child.spec.title, "child");
  assert.equal(child.publishedVersion, 1); // parent v7 must not imply child v7
  child.spec.title = "mutation";
  assert.equal(locks.resolvePinnedWorkflowDependency(snapshot, "child").spec.title, "child");
});

test("missing pinned locks fail closed while legacy workflows remain unchanged", () => {
  assert.throws(() => locks.snapshotWorkflowDependencies(parent()), /DEPENDENCY_LOCK_INVALID/);
  assert.throws(() => locks.resolvePinnedWorkflowDependency(undefined, "child"), /DEPENDENCY_LOCK_INVALID/);
  assert.throws(() => locks.resolvePinnedWorkflowDependency({}, "child"), /DEPENDENCY_LOCK_INVALID/);
  assert.equal(locks.snapshotWorkflowDependencies({ ...leaf("parent"), nodes: [call("child")] }), undefined);
});

for (const field of ["workflowId", "publishedVersion", "releaseId", "digest", "spec"]) {
  test(`lock requires ${field}`, () => {
    const dependency = pin(); delete dependency[field];
    assert.throws(() => locks.resolvePinnedWorkflowDependency({ child: dependency }, "child"), /DEPENDENCY_LOCK_INVALID/);
  });
}
for (const patch of [
  { workflowId: "other" }, { publishedVersion: 0 }, { publishedVersion: 1.5 }, { releaseId: " " },
  { digest: "sha256:" + "0".repeat(64) }, { spec: leaf("wrong") }, { spec: { ...leaf(), version: 2 } },
]) test(`rejects identity or content mismatch ${JSON.stringify(patch)}`, () => {
  assert.throws(() => locks.resolvePinnedWorkflowDependency({ child: { ...pin(), ...patch } }, "child"), /DEPENDENCY_LOCK_INVALID/);
});

test("locks the whole closure including legacy nested subworkflows without changing snapshots", () => {
  const grandchild = pin(leaf("grandchild"));
  const childSpec = { ...leaf(), nodes: [call("grandchild")], subworkflowDependencies: { grandchild } };
  const spec = { ...parent(), subworkflowDependencies: { child: pin(childSpec) } };
  const snapshot = locks.snapshotWorkflowDependencies(spec)!;
  assert.equal(snapshot.child.spec.subworkflowDependencies!.grandchild.digest, grandchild.digest);
  const incompleteChild = { ...leaf(), nodes: [call("grandchild")] };
  assert.throws(() => locks.snapshotWorkflowDependencies({ ...parent(), subworkflowDependencies: { child: pin(incompleteChild) } }), /DEPENDENCY_LOCK_INVALID/);
});

test("rejects recursive workflow identities even when every individual digest is valid", () => {
  const backEdge = pin(leaf("parent"));
  const child = pin({ ...leaf(), nodes: [call("parent", "pinned")], subworkflowDependencies: { parent: backEdge } });
  assert.throws(() => locks.snapshotWorkflowDependencies({ ...parent(), subworkflowDependencies: { child } }), /cycle/);
});

test("normalization validates explicit locks without rewriting nested hashed content", () => {
  const child = pin({ ...leaf(), config: { _business: { enabled: true } } });
  const spec = normalizeWorkflowSpec({ ...parent(), subworkflowDependencies: { child } });
  assert.equal(locks.resolvePinnedWorkflowDependency(spec.subworkflowDependencies, "child").digest, child.digest);
  const corrupt = structuredClone(spec); corrupt.subworkflowDependencies!.child.spec.title = "edited";
  assert.throws(() => normalizeWorkflowSpec(corrupt), /DEPENDENCY_LOCK_INVALID/);
});

test("release conversion uses history version when the serialized spec has no top-level version", () => {
  const dependency = dependencyFromPublishedHistory("child", release());
  assert.equal(dependency.publishedVersion, 3);
  assert.equal(dependency.releaseId, "deploy/child/#9");
  assert.equal(dependency.spec.version, undefined);
  assert.equal(locks.resolvePinnedWorkflowDependency({ child: dependency }, "child").spec.id, "child");
});
for (const action of ["edit", "save", "saved", "pull", "migration", "rollback", undefined]) {
  test(`history ${action} is not evidence of published content`, () => {
    assert.throws(() => dependencyFromPublishedHistory("child", release(leaf(), { action })), /DEPENDENCY_LOCK_INVALID/);
  });
}
for (const patch of [{ tagName: "" }, { tagName: "deploy/other/#9" }, { version: 0 }, { specJson: "not-json" }]) {
  test(`rejects unverified publication ${JSON.stringify(patch)}`, () => {
    assert.throws(() => dependencyFromPublishedHistory("child", release(leaf(), patch)), /DEPENDENCY_LOCK_INVALID/);
  });
}

test("publication requires explicit release dependencies rather than auto-selecting latest or saved", () => {
  assert.throws(() => freezeSubworkflowDependencies(parent()), /explicit/);
  const spec = { ...parent(), subworkflowDependencies: { child: pin() } };
  const frozen = freezeSubworkflowDependencies(spec);
  spec.subworkflowDependencies.child.spec.title = "edited";
  assert.equal(frozen.subworkflowDependencies.child.spec.title, "child");
  const legacy = { ...leaf("parent"), nodes: [call("child")] };
  assert.equal(freezeSubworkflowDependencies(legacy), legacy);
});

test("save acknowledgement must preserve the lock and pinned executor contract", () => {
  const spec = freezeSubworkflowDependencies({ ...parent(), subworkflowDependencies: { child: pin() } });
  assert.doesNotThrow(() => assertDependencySnapshotPreserved(spec, structuredClone(spec)));
  const stripped = structuredClone(spec); delete stripped.subworkflowDependencies;
  assert.throws(() => assertDependencySnapshotPreserved(spec, stripped), /DEPENDENCY_LOCK_INVALID/);
  const changed = structuredClone(spec); delete changed.nodes[0].executor.version;
  assert.throws(() => assertDependencySnapshotPreserved(spec, changed), /DEPENDENCY_LOCK_INVALID/);
});

// Exercise the real command boundary while replacing only its HTTP transport.
import { mkdtemp, rm, mkdir, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createServer } from "node:http";
import { execFileSync } from "node:child_process";
import type { AddressInfo } from "node:net";
import { handleDeploy } from "../src/controller/version-commands.js";

async function deployFixture(
  spec: Record<string, unknown>, save: (spec: Record<string, unknown>) => Record<string, unknown>,
  historyStatus = 200,
) {
  const root = await mkdtemp(path.join(os.tmpdir(), "fanout-publish-"));
  const packDir = path.join(root, "parent");
  await mkdir(packDir);
  const git = (args: string[]) => execFileSync("git", args, { cwd: packDir, stdio: "pipe" });
  git(["init", "--initial-branch=wf/parent"]);
  git(["config", "core.hooksPath", "/dev/null"]);
  git(["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "fixture"]);
  git(["init", "--bare", "--initial-branch=wf/parent", path.join(root, "remote.git")]);
  git(["remote", "add", "origin", path.join(root, "remote.git")]);
  const saves: Record<string, unknown>[] = [];
  const histories: Record<string, unknown>[] = [];
  const server = createServer(async (request, response) => {
    response.setHeader("Content-Type", "application/json");
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    const body = Buffer.concat(chunks).toString();
    if (request.method === "POST" && request.url === "/api/workflows/save") {
      const sent = JSON.parse(body).spec; saves.push(sent);
      response.end(JSON.stringify(save(sent)));
    } else if (request.method === "POST" && request.url === "/api/internal/deploy-history") {
      histories.push(JSON.parse(body)); response.statusCode = historyStatus;
      response.end(JSON.stringify(historyStatus === 200 ? { ok: true } : { error: "unavailable" }));
    } else if (request.url?.includes("/history/by-deploy/")) {
      const stored = histories.at(-1);
      response.end(JSON.stringify(stored ?? {}));
    } else if (request.url?.includes("/history")) {
      response.end("[]");
    } else {
      response.statusCode = 404; response.end("{}");
    }
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const result = await handleDeploy({
      packsRoot: root, clawWebBaseUrl: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
    }, "parent", { specObj: spec, packId: "parent", localAuthoritative: true });
    const yaml = await readFile(path.join(root, "parent/workflows/parent.yaml"), "utf8").catch(() => "");
    return { result, saves, histories, yaml };
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    await rm(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  }
}

test("handleDeploy refuses missing release lock before saving anything", async () => {
  const output = await deployFixture(parent(), spec => spec);
  assert.match(output.result, /DEPENDENCY_LOCK_INVALID/);
  assert.equal(output.saves.length, 0);
  assert.equal(output.histories.length, 0);
});

test("handleDeploy rejects a save API stripping the lock instead of claiming publication", async () => {
  const spec = { ...parent(), subworkflowDependencies: { child: pin() } };
  const output = await deployFixture(spec, saved => { delete saved.subworkflowDependencies; return saved; });
  assert.match(output.result, /DEPENDENCY_LOCK_INVALID/);
  assert.doesNotMatch(output.result, /✅/);
  assert.equal(output.histories.length, 0);
});

test("handleDeploy preserves byte-significant nested snapshot metadata in YAML and publication history", async () => {
  const child = pin({ ...leaf(), config: { _business: "must retain" } });
  const output = await deployFixture({ ...parent(), version: 7, subworkflowDependencies: { child } }, spec => spec);
  assert.match(output.result, /✅/);
  assert.equal(output.histories.length, 1);
  const published = JSON.parse(output.histories[0].specJson as string);
  assert.equal(published.version, undefined); // release version is separately allocated, not inferred from YAML.
  assert.equal(locks.resolvePinnedWorkflowDependency(published.subworkflowDependencies, "child").digest, child.digest);
  assert.match(output.yaml, /_business: must retain/);
});

test("handleDeploy cannot report publication success when the history repository returns false", async () => {
  const output = await deployFixture({ ...parent(), subworkflowDependencies: { child: pin() } }, spec => spec, 400);
  assert.doesNotMatch(output.result, /✅/);
  assert.match(output.result, /DEPENDENCY_LOCK_INVALID|发布记录/);
});

test("published nested snapshots must satisfy fanout schema, not only a matching hash", () => {
  const grandchild = pin(leaf("grandchild"));
  const invalidChild = { ...leaf(), nodes: [{ ...call("grandchild", "pinned"), executor: {
    ...call("grandchild", "pinned").executor, fanout: { schemaVersion: "fanout.v1", count: 5 },
  } }], subworkflowDependencies: { grandchild } } as unknown as WorkflowSpec;
  assert.throws(() => freezeSubworkflowDependencies({ ...parent(), subworkflowDependencies: { child: pin(invalidChild) } }), /count/);
});

test("one workflow ID cannot name conflicting release identities within a dependency closure", () => {
  const first = pin(leaf("shared"));
  const second = pin({ ...leaf("shared"), title: "changed" }, 2);
  const child = pin({ ...leaf(), nodes: [call("shared")], subworkflowDependencies: { shared: first } });
  const peer = pin({ ...leaf("peer"), nodes: [call("shared")], subworkflowDependencies: { shared: second } });
  assert.throws(() => locks.snapshotWorkflowDependencies({ ...parent(), nodes: [call("child", "pinned"), call("peer", "pinned")],
    subworkflowDependencies: { child, peer } }), /conflicting release/);
});

test("pack-qualified references reject a differently identified published pack", () => {
  const spec = parent(); spec.nodes[0].executor.packId = "expected";
  assert.throws(() => locks.snapshotWorkflowDependencies({ ...spec, subworkflowDependencies: { child: pin({ ...leaf(), packId: "wrong" }) } }), /pack identity/);
});

test("loop bodies and dynamic-template definitions participate in dependency closure locking", () => {
  const leafCall = call("child", "pinned");
  for (const spec of [
    { ...leaf("parent"), nodes: [{ ...call("loop"), executor: { type: "loop-group", body: [leafCall] } }] },
    { ...leaf("parent"), nodeTemplates: { batch: { body: [leafCall] } } },
  ]) assert.throws(() => locks.snapshotWorkflowDependencies(spec as WorkflowSpec), /missing explicit published dependency/);
});

test("publication readback rejects absent records, conflicting identities and changed snapshots", async () => {
  // A 409 can be swallowed as success by the old repository; an empty readback
  // must fail as well as a missing/failed write, not fall back to saved state.
  const { assertPublishedDependencyHistory } = await import("../src/controller/subworkflow-dependencies.js");
  const input = { packId: "pack", workflowId: "parent", deployNumber: 1, version: 1, tagName: "deploy/parent/#1", specJson: JSON.stringify(parent()) };
  for (const row of [null, release(parent(), { version: 2 }), release(parent(), { deployNumber: 1, version: 1, tagName: input.tagName, specJson: JSON.stringify(leaf("wrong")) })]) {
    await assert.rejects(() => assertPublishedDependencyHistory({ findByDeployNumber: async () => row }, input), /DEPENDENCY_LOCK_INVALID/);
  }
});
