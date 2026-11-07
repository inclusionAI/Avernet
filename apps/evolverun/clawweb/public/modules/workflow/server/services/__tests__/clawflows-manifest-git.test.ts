// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { execFileSync } from "node:child_process";
import { existsSync, linkSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { parse } from "yaml";
import { createLocalRemote, gitEnvironment, runGit } from "./clawflows-git-client-fixture.js";
import { cloneClawflows, commitAndPushDeploy, ensurePackManifest, prepareBranchBase, pruneLegacyPackCopy, writeWorkflowSpecToRepo } from "../clawflows-git-client.js";
import { withPreparedWorkflowSpec } from "../pack-spec-save.js";
import { resumePreparedPackCandidate } from "../pack-candidate-save.js";
import { readPackScriptSnapshot } from "../pack-script-snapshot.js";
import type { WorkflowSpec } from "../../workflow.js";
import ts from "typescript";
import { createRequire } from "node:module";
// taskguard has no installed node_modules in the clawweb workspace. Execute its
// actual TypeScript source with clawweb's yaml dependency, rather than a copy.
const manifestSource = readFileSync(new URL("../../../../../../../taskguard/src/packs/manifest.ts", import.meta.url), "utf8");
const js = ts.transpileModule(manifestSource, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const taskguardExports: { normalizePackManifest?: (raw: unknown) => unknown } = {};
new Function("require", "exports", js)(createRequire(import.meta.url), taskguardExports);
const normalizePackManifest = taskguardExports.normalizePackManifest!;

const author = { name: "Test", email: "test@example.invalid" };
const spec: WorkflowSpec = { id: "flow", title: "Updated", nodes: [] };
let root: string; let bare: string; let repo: string;
function put(name: string, content: string) { const file = path.join(repo, name); mkdirSync(path.dirname(file), { recursive: true }); writeFileSync(file, content); }
async function clone() { const dir = mkdtempSync(path.join(root, "clone-")); await cloneClawflows(dir, { remoteUrl: bare, username: "Test", email: author.email }); return dir; }
// These tests exercise the mutable spec/manifest helpers, not the retired save
// writer. Snapshot their output as fixture data for reader/TaskGuard assertions;
// production save ordering and leases are exercised in the prepared-save suites.
async function publishManifestFixture(work: string, workflowId: string, _note: string, _author: typeof author) {
  const ref = `refs/heads/wf/${workflowId}`;
  const expected = runGit(work, ['ls-remote', '--refs', 'origin', ref]).split('\t')[0] || '';
  runGit(work, ['add', '.']); runGit(work, ['commit', '-m', 'manifest helper fixture']);
  const commit = runGit(work, ['rev-parse', 'HEAD']);
  runGit(work, ['push', `--force-with-lease=${ref}:${expected}`, 'origin', `${commit}:${ref}`]);
  return { branch: `wf/${workflowId}`, commit };
}
function readFixtureVersion(reader: string, workflowId: string, packId: string, version: 'draft' | 'current') {
  const activeTag = `deploy/${workflowId}/#1`;
  if (version === 'current') runGit(reader, ['update-ref', 'refs/pack-version/active', `${activeTag}^{commit}`]);
  return readPackScriptSnapshot(reader, workflowId, packId, version, version === 'current' ? { activeTag } : undefined);
}
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), "pack-write-test-")); bare = createLocalRemote(root); repo = mkdtempSync(path.join(root, "seed-"));
  runGit(repo, ["init"]); runGit(repo, ["config", "user.name", "Test"]); runGit(repo, ["config", "user.email", author.email]);
  put("README", "seed\n"); runGit(repo, ["add", "."]); runGit(repo, ["commit", "-m", "initial"]);
});
afterEach(() => rmSync(root, { recursive: true, force: true }));

describe("pack manifest save/deploy", () => {
  it.each([
    ["", true, "save"], ["", true, "deploy"],
    ["pack/", true, "save"], ["pack/", true, "deploy"],
    ["packs/pack/", true, "save"], ["packs/pack/", true, "deploy"],
  ])("round trips %s layout (object=%s) after %s", async (layout, object, operation) => {
    const target = object ? "custom/flow.yaml" : "workflows/flow.yaml";
    const manifest = `id: pack\nversion: "1.0.0"\nworkflows:\n  - ${object ? `id: flow\n    file: ${target}` : "flow"}\n  - id: other\n    file: custom/other.yaml\n`;
    put(`${layout}workflow.pack.yaml`, manifest); put(`${layout}${target}`, "id: flow\ntitle: Old\nnodes: []\n");
    put(`${layout}custom/other.yaml`, "id: other\ntitle: Keep\n"); put(`${layout}scripts/run.sh`, "echo keep\n");
    runGit(repo, ["init"]); runGit(repo, ["config", "user.name", "Test"]); runGit(repo, ["config", "user.email", author.email]);
    runGit(repo, ["add", "."]); runGit(repo, ["commit", "-m", "seed"]); runGit(repo, ["push", bare, "HEAD:refs/heads/wf/flow"]);
    const work = await clone();
    if (operation === "save") await prepareBranchBase(work, "wf/flow", "wf/flow");
    else await prepareBranchBase(work, "wf/flow");
    await writeWorkflowSpecToRepo(work, "pack", "flow", spec); await ensurePackManifest(work, "pack", "flow");
    await pruneLegacyPackCopy(work, "pack");
    if (operation === "save") await publishManifestFixture(work, "flow", "save", author);
    else await commitAndPushDeploy(work, "flow", 1, 1, "deploy", author);
    const check = await clone(); const snapshot = await readPackScriptSnapshot(check, "flow", "pack");
    expect(snapshot.baseRef).toBe("origin/wf/flow");
    expect(snapshot.files[0].content).toBe("echo keep\n");
    const ref = operation === "save" ? "wf/flow" : "deploy/flow/#1";
    const written = parse(runGit(bare, ["show", `${ref}:${layout}workflow.pack.yaml`]));
    expect(normalizePackManifest(written)).toMatchObject({ id: "pack", version: "1.0.0" });
    expect(written.workflows).toHaveLength(2);
    if (object) expect(written.workflows[0]).toEqual({ id: "flow", file: target });
    expect(runGit(bare, ["show", `${ref}:${layout}${target}`])).toContain("Updated");
    expect(runGit(bare, ["show", `${ref}:${layout}custom/other.yaml`])).toContain("Keep");
  });
  it.each([
    ["conflicting manifests", { "workflow.pack.yaml": "id: pack\nworkflows: [flow]\n", "pack/workflow.pack.yaml": "id: pack\nworkflows: [flow]\n", "packs/pack/valuable": "keep" }],
    ["root and nested manifests", { "workflow.pack.yaml": "id: pack\nworkflows: [flow]\n", "packs/pack/workflow.pack.yaml": "invalid: [\n" }],
    ["invalid manifest", { "workflow.pack.yaml": "workflows: [\n" }],
    ["duplicate workflow", { "workflow.pack.yaml": "id: pack\nversion: '1.0.0'\nworkflows:\n  - {id: flow, file: workflows/flow.yaml}\n  - {id: flow, file: workflows/flow.yaml}\n" }],
    ["shared path", { "workflow.pack.yaml": "id: pack\nversion: '1.0.0'\nworkflows:\n  - id: flow\n    file: shared.yaml\n  - id: other\n    file: shared.yaml\n", "shared.yaml": "id: other\n" }],
    ["unsafe path", { "workflow.pack.yaml": "id: pack\nversion: '1.0.0'\nworkflows:\n  - id: flow\n    file: ../escape.yaml\n" }],
    ["different workflow", { "workflow.pack.yaml": "id: pack\nversion: '1.0.0'\nworkflows:\n  - {id: flow, file: workflows/flow.yaml}\n", "workflows/flow.yaml": "id: other\n" }],
  ])("rejects %s without changing files", async (_label, files) => {
    for (const [name, content] of Object.entries(files)) put(name, content);
    await expect(writeWorkflowSpecToRepo(repo, "pack", "flow", spec)).rejects.toThrow();
    await expect(ensurePackManifest(repo, "pack", "flow")).rejects.toThrow();
    for (const [name, content] of Object.entries(files)) expect(readFileSync(path.join(repo, name), "utf8")).toBe(content);
  });
  it("does not delete a legacy directory containing unrelated data", async () => {
    put("pack/workflow.pack.yaml", "id: pack\nworkflows: [flow]\n"); put("packs/pack/valuable", "keep");
    expect(await pruneLegacyPackCopy(repo, "pack")).toBe(false); expect(readFileSync(path.join(repo, "packs/pack/valuable"), "utf8")).toBe("keep");
  });
  it("adds a missing workflow while preserving existing object members", async () => {
    put("workflow.pack.yaml", "# keep comment\nid: pack\nversion: '1.0.0'\nworkflows:\n  - id: other\n    file: custom/other.yaml\n");
    put("custom/other.yaml", "id: other\n");
    await writeWorkflowSpecToRepo(repo, "pack", "flow", spec);
    await ensurePackManifest(repo, "pack", "flow");
    await ensurePackManifest(repo, "pack", "flow");
    const manifest = readFileSync(path.join(repo, "workflow.pack.yaml"), "utf8");
    expect(manifest).toContain("# keep comment");
    expect(parse(manifest).workflows).toEqual([{ id: "other", file: "custom/other.yaml" }, { id: "flow", file: "workflows/flow.yaml" }]);
  });
  it("rejects symlink paths without writing through them", async () => {
    put("workflow.pack.yaml", "id: pack\nversion: '1.0.0'\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n");
    const outside = mkdtempSync(path.join(root, "outside-"));
    symlinkSync(outside, path.join(repo, "workflows"));
    await expect(writeWorkflowSpecToRepo(repo, "pack", "flow", spec)).rejects.toThrow(/symlink/i);
    await expect(ensurePackManifest(repo, "pack", "flow")).rejects.toThrow(/symlink/i);
    expect(readFileSync(path.join(repo, "workflow.pack.yaml"), "utf8")).toBe("id: pack\nversion: '1.0.0'\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n");
  });
  it("refuses a stale prepared save without overwriting another writer", async () => {
    const config = { remoteUrl: bare, username: author.name, email: author.email };
    const first = await withPreparedWorkflowSpec({ workflowId: 'flow', packId: 'pack', spec: { ...spec, title: 'First writer' }, config, author, createOnly: true }, async p => p);
    const stale = await withPreparedWorkflowSpec({ workflowId: 'flow', packId: 'pack', spec, config, author, createOnly: true }, async p => p);
    await resumePreparedPackCandidate(first, config);
    await expect(resumePreparedPackCandidate(stale, config)).rejects.toThrow();
    expect(runGit(root, ['--git-dir', bare, 'rev-parse', 'refs/heads/wf/flow'])).toBe(first.commit);
    expect(runGit(bare, ['show', 'wf/flow:packs/pack/workflows/flow.yaml'])).toContain('First writer');
  });

});


describe("runtime manifest compatibility and gitlinks", () => {
  it("creates a runtime-valid manifest visible in a deploy tag", async () => {
    const work = await clone();
    await prepareBranchBase(work, "wf/flow");
    await writeWorkflowSpecToRepo(work, "pack", "flow", spec);
    await ensurePackManifest(work, "pack", "flow");
    await commitAndPushDeploy(work, "flow", 1, 1, "deploy", author);
    const manifest = parse(runGit(bare, ["show", "deploy/flow/#1:packs/pack/workflow.pack.yaml"]));
    expect(normalizePackManifest(manifest)).toMatchObject({ id: "pack", version: "1.0.0", workflows: [{ id: "flow", file: "workflows/flow.yaml" }] });
  });
  it("appends an object member without losing runtime metadata or comments", async () => {
    const work = await clone();
    await prepareBranchBase(work, "wf/flow");
    const content = "# preserve\nid: pack\nversion: '2.0.0'\nworkflows:\n  - id: other\n    file: custom/other.yaml\n";
    writeFileSync(path.join(work, "workflow.pack.yaml"), content);
    await writeWorkflowSpecToRepo(work, "pack", "flow", spec);
    await ensurePackManifest(work, "pack", "flow");
    await commitAndPushDeploy(work, "flow", 1, 1, "deploy", author);
    const saved = runGit(bare, ["show", "deploy/flow/#1:workflow.pack.yaml"]);
    expect(saved).toContain("# preserve");
    expect(normalizePackManifest(parse(saved))).toMatchObject({ version: "2.0.0", workflows: [{ id: "other", file: "custom/other.yaml" }, { id: "flow", file: "workflows/flow.yaml" }] });
  });
  it.each([
    "id: pack\nversion: '1.0.0'\nworkflows: [flow]\n",
    "id: pack\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n",
    "id: pack\nversion: '1.0.0'\nworkflows:\n  - {id: flow, file: workflows/flow.yaml}\n  - other\n",
  ])("refuses legacy or versionless manifests before writing", async (content) => {
    const work = await clone(); writeFileSync(path.join(work, "workflow.pack.yaml"), content);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/manifest/i);
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/manifest/i);
    expect(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8")).toBe(content);
  });
  it.each([
    ["save", "workflows", false], ["deploy", "workflows", false],
    ["save", "packs/pack", false], ["deploy", "packs/pack", false],
    ["save", "workflows", true], ["deploy", "workflows", true],
  ])("rejects submodule on %s at %s (index removed=%s) without publishing", async (operation, location, removeIndex) => {
    const sub = path.join(root, "sub"); mkdirSync(sub);
    runGit(sub, ["init"]); runGit(sub, ["config", "user.name", "Test"]); runGit(sub, ["config", "user.email", author.email]);
    writeFileSync(path.join(sub, "kept"), "keep"); runGit(sub, ["add", "."]); runGit(sub, ["commit", "-m", "submodule"]);
    runGit(repo, ["init"]); runGit(repo, ["config", "user.name", "Test"]); runGit(repo, ["config", "user.email", author.email]);
    if (location === "workflows") put("workflow.pack.yaml", "id: pack\nversion: '1.0.0'\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n");
    runGit(repo, ["-c", "protocol.file.allow=always", "submodule", "add", sub, location]);
    runGit(repo, ["add", "."]); runGit(repo, ["commit", "-m", "seed"]); runGit(repo, ["push", bare, "HEAD:refs/heads/wf/flow"]);
    const before = runGit(root, ["--git-dir", bare, "rev-parse", "refs/heads/wf/flow"]);
    const work = await clone(); await prepareBranchBase(work, "wf/flow", "wf/flow");
    if (removeIndex) runGit(work, ["rm", "--cached", "-f", "--", location]);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/gitlink|submodule/i);
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/gitlink|submodule/i);
    expect(existsSync(path.join(work, location, "flow.yaml"))).toBe(false);
    expect(runGit(root, ["--git-dir", bare, "rev-parse", "refs/heads/wf/flow"])).toBe(before);
    expect(runGit(root, ["--git-dir", bare, "for-each-ref", "--format=%(refname)", "refs/heads/saved/flow", "refs/tags/deploy/flow/"])).toBe("");
  });
  it("rejects an index-only gitlink even when HEAD has no submodule", async () => {
    const sub = path.join(root, "staged-sub"); mkdirSync(sub);
    runGit(sub, ["init"]); runGit(sub, ["config", "user.name", "Test"]); runGit(sub, ["config", "user.email", author.email]);
    writeFileSync(path.join(sub, "kept"), "keep"); runGit(sub, ["add", "."]); runGit(sub, ["commit", "-m", "sub"]);
    const work = await clone(); await prepareBranchBase(work, "wf/flow");
    writeFileSync(path.join(work, "workflow.pack.yaml"), "id: pack\nversion: '1.0.0'\nworkflows:\n  - {id: flow, file: workflows/flow.yaml}\n");
    runGit(work, ["-c", "protocol.file.allow=always", "submodule", "add", sub, "workflows"]);
    expect(runGit(work, ["ls-files", "--stage", "workflows"])).toMatch(/^160000 /);
    expect(runGit(work, ["ls-tree", "HEAD", "workflows"])).toBe("");
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/gitlink|submodule/i);
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/gitlink|submodule/i);
    expect(existsSync(path.join(work, "workflows/flow.yaml"))).toBe(false);
    expect(runGit(root, ["--git-dir", bare, "for-each-ref", "--format=%(refname)", "refs/heads/wf/flow", "refs/tags/deploy/flow/"])).toBe("");
  });

});


describe("unborn, legacy migration, and resource safety", () => {
  it.each(["save", "deploy"])("creates a first %s in a genuinely empty bare remote", async operation => {
    const empty = path.join(root, `empty-${operation}.git`);
    runGit(root, ["init", "--bare", "--initial-branch=main", empty]);
    const work = mkdtempSync(path.join(root, "empty-clone-"));
    await cloneClawflows(work, { remoteUrl: empty, username: "Test", email: author.email });
    await prepareBranchBase(work, "wf/flow");
    await writeWorkflowSpecToRepo(work, "pack", "flow", spec);
    await ensurePackManifest(work, "pack", "flow");
    const result = operation === "save" ? await publishManifestFixture(work, "flow", "save", author)
      : await commitAndPushDeploy(work, "flow", 1, 1, "deploy", author);
    const ref = operation === "save" ? "wf/flow" : "deploy/flow/#1";
    const manifest = parse(runGit(root, ["--git-dir", empty, "show", `${ref}:packs/pack/workflow.pack.yaml`]));
    expect(normalizePackManifest(manifest)).toMatchObject({ id: "pack", version: "1.0.0", workflows: [{ id: "flow", file: "workflows/flow.yaml" }] });
    expect(runGit(root, ["--git-dir", empty, "rev-parse", `${ref}^{commit}`])).toBe(result.commit);
  });
  it.each(["save", "deploy"])("safely migrates all string members and preserves other data on %s", async operation => {
    const work = await clone();
    await prepareBranchBase(work, "wf/flow");
    const putWork = (name: string, text: string) => { const file = path.join(work, name); mkdirSync(path.dirname(file), { recursive: true }); writeFileSync(file, text); };
    putWork("workflow.pack.yaml", "# pack comment\nid: pack\ntitle: Legacy pack\nworkflows:\n  - flow # first member\n  - other\n");
    putWork("workflows/flow.yaml", "id: flow\nnodes: []\n"); putWork("workflows/other.yaml", "id: other\nnodes: []\n");
    putWork("scripts/keep.sh", "echo safe\n");
    await writeWorkflowSpecToRepo(work, "pack", "flow", spec); await ensurePackManifest(work, "pack", "flow");
    if (operation === "save") await publishManifestFixture(work, "flow", "save", author); else await commitAndPushDeploy(work, "flow", 1, 1, "deploy", author);
    const ref = operation === "save" ? "wf/flow" : "deploy/flow/#1";
    const manifestText = runGit(root, ["--git-dir", bare, "show", `${ref}:workflow.pack.yaml`]);
    expect(manifestText).toContain("# pack comment"); expect(manifestText).toContain("# first member");
    expect(normalizePackManifest(parse(manifestText))).toMatchObject({ id: "pack", title: "Legacy pack", version: "1.0.0", workflows: [{ id: "flow", file: "workflows/flow.yaml" }, { id: "other", file: "workflows/other.yaml" }] });
    expect(runGit(root, ["--git-dir", bare, "show", `${ref}:workflows/other.yaml`])).toContain("id: other");
    expect(runGit(root, ["--git-dir", bare, "show", `${ref}:scripts/keep.sh`])).toBe("echo safe");
  });
  it("retains a numeric version accepted by TaskGuard", async () => {
    const work = await clone();
    writeFileSync(path.join(work, "workflow.pack.yaml"), "id: pack\nversion: 2\nworkflows:\n  - {id: other, file: workflows/other.yaml}\n");
    await writeWorkflowSpecToRepo(work, "pack", "flow", spec); await ensurePackManifest(work, "pack", "flow");
    const raw = parse(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8"));
    expect(raw.version).toBe(2); expect(normalizePackManifest(raw)).toMatchObject({ version: "2" });
  });
  it.each([
    ["missing", "workflows: [flow, other]\n", { "workflows/flow.yaml": "id: flow\nnodes: []\n" }],
    ["wrong id", "workflows: [flow, other]\n", { "workflows/flow.yaml": "id: flow\nnodes: []\n", "workflows/other.yaml": "id: different\nnodes: []\n" }],
    ["duplicate", "workflows: [flow, flow]\n", { "workflows/flow.yaml": "id: flow\nnodes: []\n" }],
    ["resource conflict", "actions: [{id: act, type: shell, root: workflows/flow.yaml}]\nworkflows: [flow]\n", { "workflows/flow.yaml": "id: flow\nnodes: []\n" }],
    ["invalid spec", "workflows: [flow]\n", { "workflows/flow.yaml": "id: flow\ndata: keep\n" }],
  ])("rejects unsafe legacy %s before changing anything", async (_name, entries, files) => {
    const work = await clone(); const manifest = `# preserve\nid: pack\n${entries}`; writeFileSync(path.join(work, "workflow.pack.yaml"), manifest);
    for (const [name, content] of Object.entries(files)) { const file = path.join(work, name); mkdirSync(path.dirname(file), { recursive: true }); writeFileSync(file, content); }
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow();
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow();
    expect(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8")).toBe(manifest);
    for (const [name, content] of Object.entries(files)) expect(readFileSync(path.join(work, name), "utf8")).toBe(content);
  });
  it.each([
    ["scripts/data.yaml", "", "id: flow\nnodes: []\n"],
    ["data/settings.yaml", "", "id: flow\nsettings: keep\n"],
    ["assets/data.yaml", "actions: [{id: act, type: shell, root: assets}]\n", "id: flow\nnodes: []\n"],
    ["actions/flow.yaml", "actions:\n  - id: act\n    type: shell\n    root: actions\n", "id: flow\nnodes: []\n"],
  ])("never overwrites a resource at %s", async (target, resource, old) => {
    const work = await clone(); const file = path.join(work, target); mkdirSync(path.dirname(file), { recursive: true }); writeFileSync(file, old);
    const manifest = `id: pack\nversion: '1.0.0'\n${resource}workflows:\n  - {id: flow, file: ${target}}\n`; writeFileSync(path.join(work, "workflow.pack.yaml"), manifest);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow();
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow();
    expect(readFileSync(file, "utf8")).toBe(old); expect(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8")).toBe(manifest);
  });
  it("still checks the index when HEAD is unborn", async () => {
    const empty = path.join(root, "unborn-index.git"); runGit(root, ["init", "--bare", "--initial-branch=main", empty]);
    const work = mkdtempSync(path.join(root, "unborn-index-"));
    await cloneClawflows(work, { remoteUrl: empty, username: "Test", email: author.email });
    await prepareBranchBase(work, "wf/flow");
    const oid = runGit(repo, ["rev-parse", "HEAD"]);
    runGit(work, ["update-index", "--add", "--cacheinfo", `160000,${oid},packs/pack`]);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/gitlink|submodule/i);
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/gitlink|submodule/i);
    expect(existsSync(path.join(work, "packs/pack/workflow.pack.yaml"))).toBe(false);
  });
  it("fails closed on a corrupt existing HEAD rather than treating it as unborn", async () => {
    const work = await clone();
    writeFileSync(path.join(work, ".git/refs/heads/main"), `${"1".repeat(40)}\n`);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow();
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow();
    expect(existsSync(path.join(work, "packs/pack/workflow.pack.yaml"))).toBe(false);
  });
  it.each(["symlink", "hardlink"]) ("refuses legacy %s references without touching either file", async kind => {
    const work = await clone();
    writeFileSync(path.join(work, "workflow.pack.yaml"), "id: pack\nworkflows: [flow]\n");
    mkdirSync(path.join(work, "workflows")); writeFileSync(path.join(work, "source.yaml"), "id: flow\nnodes: []\n");
    const file = path.join(work, "workflows/flow.yaml");
    if (kind === "symlink") symlinkSync(path.join(work, "source.yaml"), file); else linkSync(path.join(work, "source.yaml"), file);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow();
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow();
    expect(readFileSync(path.join(work, "source.yaml"), "utf8")).toBe("id: flow\nnodes: []\n");
    expect(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8")).toBe("id: pack\nworkflows: [flow]\n");
  });

  it("does not rewrite a hardlinked legacy manifest", async () => {
    const work = await clone(); const content = "id: pack\nworkflows: [flow]\n";
    writeFileSync(path.join(work, "other-manifest.yaml"), content);
    linkSync(path.join(work, "other-manifest.yaml"), path.join(work, "workflow.pack.yaml"));
    mkdirSync(path.join(work, "workflows")); writeFileSync(path.join(work, "workflows/flow.yaml"), "id: flow\nnodes: []\n");
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/manifest/i);
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/manifest/i);
    expect(readFileSync(path.join(work, "other-manifest.yaml"), "utf8")).toBe(content);
  });
  it("checks a non-target legacy member for gitlinks before writing the target", async () => {
    const work = await clone();
    writeFileSync(path.join(work, "workflow.pack.yaml"), "id: pack\nworkflows: [flow, other]\n");
    mkdirSync(path.join(work, "workflows")); writeFileSync(path.join(work, "workflows/flow.yaml"), "id: flow\nnodes: []\n");
    const oid = runGit(repo, ["rev-parse", "HEAD"]);
    runGit(work, ["update-index", "--add", "--cacheinfo", `160000,${oid},workflows/other.yaml`]);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/gitlink|submodule/i);
    expect(readFileSync(path.join(work, "workflows/flow.yaml"), "utf8")).toBe("id: flow\nnodes: []\n");
  });

  it("rejects another member that aliases the target through a symlink", async () => {
    const work = await clone(); mkdirSync(path.join(work, "custom"));
    writeFileSync(path.join(work, "custom/flow.yaml"), "id: flow\nnodes: []\n");
    symlinkSync("flow.yaml", path.join(work, "custom/other.yaml"));
    const manifest = "id: pack\nversion: '1.0.0'\nworkflows:\n  - {id: flow, file: custom/flow.yaml}\n  - {id: other, file: custom/other.yaml}\n";
    writeFileSync(path.join(work, "workflow.pack.yaml"), manifest);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow();
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow();
    expect(readFileSync(path.join(work, "custom/flow.yaml"), "utf8")).toBe("id: flow\nnodes: []\n");
    expect(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8")).toBe(manifest);
  });

  it("does not create a missing target path already assigned to another member", async () => {
    const work = await clone();
    const manifest = "id: pack\nversion: '1.0.0'\nworkflows:\n  - {id: other, file: workflows/flow.yaml}\n";
    writeFileSync(path.join(work, "workflow.pack.yaml"), manifest);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/shared|conflict/i);
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/shared|conflict/i);
    expect(existsSync(path.join(work, "workflows/flow.yaml"))).toBe(false);
    expect(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8")).toBe(manifest);
  });

});

describe("legacy additions, contract resource paths, and YAML aliases", () => {
  it.each(["save", "deploy"])("adds a new workflow to an existing legacy pack on %s", async operation => {
    put("workflow.pack.yaml", "# preserve\nid: pack\nworkflows: [other]\n");
    put("workflows/other.yaml", "id: other\nnodes: []\n");
    put("scripts/keep.sh", "echo untouched\n");
    runGit(repo, ["add", "."]); runGit(repo, ["commit", "-m", "legacy"]);
    runGit(repo, ["push", bare, "HEAD:refs/heads/wf/flow"]);
    const work = await clone();
    await prepareBranchBase(work, "wf/flow", "wf/flow");
    await writeWorkflowSpecToRepo(work, "pack", "flow", spec);
    await ensurePackManifest(work, "pack", "flow");
    if (operation === "save") await publishManifestFixture(work, "flow", "save", author);
    else await commitAndPushDeploy(work, "flow", 1, 1, "deploy", author);
    const ref = operation === "save" ? "wf/flow" : "deploy/flow/#1";
    const text = runGit(bare, ["show", `${ref}:workflow.pack.yaml`]);
    expect(text).toContain("# preserve");
    expect(normalizePackManifest(parse(text))).toMatchObject({ version: "1.0.0", workflows: [
      { id: "other", file: "workflows/other.yaml" }, { id: "flow", file: "workflows/flow.yaml" },
    ] });
    expect(runGit(bare, ["show", `${ref}:workflows/other.yaml`])).toBe("id: other\nnodes: []");
    expect(runGit(bare, ["show", `${ref}:workflows/flow.yaml`])).toContain("id: flow");
    expect(runGit(bare, ["show", `${ref}:scripts/keep.sh`])).toBe("echo untouched");
    const check = await clone();
    expect((await readPackScriptSnapshot(check, "flow", "pack")).files[0].content).toBe("echo untouched\n");
  });
  it.each(["object", "legacy"])("ignores skill, facade, action identifiers and unknown metadata in %s manifests", async kind => {
    const work = await clone(); mkdirSync(path.join(work, "workflows"));
    writeFileSync(path.join(work, "workflows/other.yaml"), "id: other\nnodes: []\n");
    const entry = kind === "legacy" ? "other" : "{id: other, file: workflows/other.yaml}";
    const manifest = `id: pack\nversion: '1.0.0'\nworkflows: [${entry}]\nskills:\n  required: [workflows]\nfacades:\n  - command: workflows\n    defaultWorkflow: other\n    help:\n      summary: workflows/flow.yaml\nactions:\n  - id: workflows\n    type: workflows\n    root: bin\n    commands:\n      flow: workflows/flow.yaml\nmeta:\n  root: workflows\n  file: workflows/flow.yaml\n`;
    writeFileSync(path.join(work, "workflow.pack.yaml"), manifest);
    await writeWorkflowSpecToRepo(work, "pack", "flow", spec); await ensurePackManifest(work, "pack", "flow");
    const written = parse(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8"));
    expect(normalizePackManifest(written)).toMatchObject({ skills: { required: ["workflows"] }, facades: [{ command: "workflows", defaultWorkflow: "other" }] });
    expect(written.meta).toEqual({ root: "workflows", file: "workflows/flow.yaml" });
    expect(written.actions[0].commands.flow).toBe("workflows/flow.yaml");
  });
  it.each([
    ["root directory", "root: workflows\n    commands: {run: flow.yaml}"],
    ["root equals target", "root: workflows/flow.yaml"],
    ["escaping command", "root: bin\n    commands: {run: ../../workflows/flow.yaml}"],
    ["invalid commands", "root: bin\n    commands: [bad]"],
  ])("rejects action %s before writing", async (_label, fields) => {
    const work = await clone();
    const manifest = `id: pack\nversion: '1.0.0'\nworkflows: [{id: other, file: custom/other.yaml}]\nactions:\n  - id: act\n    type: shell\n    ${fields}\n`;
    writeFileSync(path.join(work, "workflow.pack.yaml"), manifest);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow();
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow();
    expect(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8")).toBe(manifest);
    expect(existsSync(path.join(work, "workflows/flow.yaml"))).toBe(false);
  });
  it.each([
    "workflows: &shared\n  - {id: other, file: custom/other.yaml}\nmeta: *shared\n",
    "meta: &shared\n  - {id: other, file: custom/other.yaml}\nworkflows: *shared\n",
  ])("refuses aliased object workflows before any write", async content => {
    const work = await clone(); const manifest = `id: pack\nversion: '1.0.0'\n${content}`;
    writeFileSync(path.join(work, "workflow.pack.yaml"), manifest);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/alias|anchor/i);
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/alias|anchor/i);
    expect(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8")).toBe(manifest);
    expect(existsSync(path.join(work, "workflows/flow.yaml"))).toBe(false);
  });
  it("refuses a new legacy member if an original member is missing", async () => {
    const work = await clone(); const manifest = "id: pack\nworkflows: [other]\n";
    writeFileSync(path.join(work, "workflow.pack.yaml"), manifest);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/missing/i);
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/missing/i);
    expect(existsSync(path.join(work, "workflows/flow.yaml"))).toBe(false);
    expect(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8")).toBe(manifest);
  });
  it("preserves an aliased object manifest byte-for-byte when no manifest edit is needed", async () => {
    const work = await clone(); mkdirSync(path.join(work, "workflows"));
    writeFileSync(path.join(work, "workflows/flow.yaml"), "id: flow\nnodes: []\n");
    const manifest = "id: pack\nversion: '1.0.0'\nworkflows: &shared\n  - {id: flow, file: workflows/flow.yaml}\nmeta: *shared\n";
    writeFileSync(path.join(work, "workflow.pack.yaml"), manifest);
    await writeWorkflowSpecToRepo(work, "pack", "flow", spec); await ensurePackManifest(work, "pack", "flow");
    expect(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8")).toBe(manifest);
    expect(readFileSync(path.join(work, "workflows/flow.yaml"), "utf8")).toContain("Updated");
  });

  it.each(["root", "command"])("rejects a symlinked action %s that aliases the workflow", async kind => {
    const work = await clone(); mkdirSync(path.join(work, "workflows"));
    const content = "id: flow\nnodes: []\n"; writeFileSync(path.join(work, "workflows/flow.yaml"), content);
    if (kind === "root") symlinkSync("workflows", path.join(work, "bin"));
    else { mkdirSync(path.join(work, "bin")); symlinkSync("../workflows/flow.yaml", path.join(work, "bin/flow.yaml")); }
    const manifest = "id: pack\nversion: '1.0.0'\nworkflows: [{id: flow, file: workflows/flow.yaml}]\nactions:\n  - id: act\n    type: shell\n    root: bin\n    commands: {run: flow.yaml}\n";
    writeFileSync(path.join(work, "workflow.pack.yaml"), manifest);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/symlink/i);
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/symlink/i);
    expect(readFileSync(path.join(work, "workflows/flow.yaml"), "utf8")).toBe(content);
  });

});

describe("manifest candidates are selected by pack identity", () => {
  const rootManifest = "# belongs to someone else\nid: root-pack\nversion: '2.0.0'\nworkflows:\n  - {id: root-flow, file: workflows/root.yaml}\n";
  const targetManifest = "id: pack\nversion: '1.0.0'\nworkflows:\n  - {id: flow, file: workflows/flow.yaml}\n";
  it.each(["save", "deploy"])("updates a nested target without changing the different root pack on %s", async operation => {
    put("workflow.pack.yaml", rootManifest); put("workflows/root.yaml", "id: root-flow\nnodes: []\n");
    put("scripts/root.sh", "echo root untouched\n"); put("packs/pack/workflow.pack.yaml", targetManifest);
    put("packs/pack/workflows/flow.yaml", "id: flow\ntitle: Old\nnodes: []\n");
    put("packs/pack/scripts/target.sh", "echo target untouched\n");
    runGit(repo, ["add", "."]); runGit(repo, ["commit", "-m", "two packs"]);
    runGit(repo, ["push", bare, "HEAD:refs/heads/wf/flow"]);
    const work = await clone();
    await prepareBranchBase(work, "wf/flow", "wf/flow");
    await writeWorkflowSpecToRepo(work, "pack", "flow", spec); await ensurePackManifest(work, "pack", "flow");
    await pruneLegacyPackCopy(work, "pack");
    if (operation === "save") await publishManifestFixture(work, "flow", "save", author);
    else await commitAndPushDeploy(work, "flow", 1, 1, "deploy", author);
    const ref = operation === "save" ? "wf/flow" : "deploy/flow/#1";
    const showBytes = (name: string) => execFileSync("git", ["--git-dir", bare, "show", `${ref}:${name}`], { cwd: root, env: gitEnvironment() });
    expect(showBytes("workflow.pack.yaml")).toEqual(Buffer.from(rootManifest));
    expect(showBytes("workflows/root.yaml")).toEqual(Buffer.from("id: root-flow\nnodes: []\n"));
    expect(showBytes("scripts/root.sh")).toEqual(Buffer.from("echo root untouched\n"));
    expect(runGit(bare, ["show", `${ref}:packs/pack/scripts/target.sh`])).toBe("echo target untouched");
    expect(runGit(bare, ["show", `${ref}:packs/pack/workflows/flow.yaml`])).toContain("Updated");
    expect(normalizePackManifest(parse(runGit(bare, ["show", `${ref}:packs/pack/workflow.pack.yaml`])))).toMatchObject({ id: "pack" });
    const reader = await clone();
    const snapshot = await readFixtureVersion(reader, "flow", "pack", operation === "save" ? "draft" : "current");
    expect(snapshot.files.map(file => file.content)).toEqual(["echo target untouched\n"]);
  });
  it("can create a new nested target without replacing a different root pack", async () => {
    put("workflow.pack.yaml", rootManifest);
    await writeWorkflowSpecToRepo(repo, "pack", "flow", spec); await ensurePackManifest(repo, "pack", "flow");
    expect(readFileSync(path.join(repo, "workflow.pack.yaml"), "utf8")).toBe(rootManifest);
    expect(normalizePackManifest(parse(readFileSync(path.join(repo, "packs/pack/workflow.pack.yaml"), "utf8")))).toMatchObject({ id: "pack" });
  });
  it("refuses to create a target over another pack's existing nested manifest", async () => {
    const other = rootManifest.replace("root-pack", "other-pack");
    put("packs/pack/workflow.pack.yaml", other);
    await expect(writeWorkflowSpecToRepo(repo, "pack", "flow", spec)).rejects.toThrow(/manifest/i);
    await expect(ensurePackManifest(repo, "pack", "flow")).rejects.toThrow(/manifest/i);
    expect(readFileSync(path.join(repo, "packs/pack/workflow.pack.yaml"), "utf8")).toBe(other);
  });
  it("prefers the root manifest and preserves nested Pack resources", async () => {
    put("workflow.pack.yaml", targetManifest); put("packs/pack/workflow.pack.yaml", targetManifest);
    put("packs/pack/workflows/flow.yaml", "id: flow\nnodes: []\n");
    await writeWorkflowSpecToRepo(repo, "pack", "flow", spec);
    await ensurePackManifest(repo, "pack", "flow");
    expect(readFileSync(path.join(repo, "workflow.pack.yaml"), "utf8")).toBe(targetManifest);
    expect(normalizePackManifest(parse(readFileSync(path.join(repo, "workflow.pack.yaml"), "utf8")))).toMatchObject({ id: "pack" });
    expect(readFileSync(path.join(repo, "workflows/flow.yaml"), "utf8")).toContain("Updated");
    expect(readFileSync(path.join(repo, "packs/pack/workflow.pack.yaml"), "utf8")).toBe(targetManifest);
    expect(readFileSync(path.join(repo, "packs/pack/workflows/flow.yaml"), "utf8")).toBe("id: flow\nnodes: []\n");
  });
  it.each([
    ["invalid yaml", Buffer.from("id: [\n")],
    ["missing identity", Buffer.from("version: '1.0.0'\nworkflows: [x]\n")],
    ["invalid workflow shape", Buffer.from("id: root-pack\nversion: '1.0.0'\nworkflows: [42]\n")],
    ["invalid version", Buffer.from("id: root-pack\nversion: []\nworkflows: [x]\n")],
    ["invalid actions metadata", Buffer.from("id: root-pack\nversion: '1'\nworkflows: [{id: x, file: workflows/x.yaml}]\nactions: wrong\n")],
    ["invalid facade metadata", Buffer.from("id: root-pack\nversion: '1'\nworkflows: [{id: x, file: workflows/x.yaml}]\nfacades: [{command: xx, defaultWorkflow: missing}]\n")],
    ["invalid skill metadata", Buffer.from("id: root-pack\nversion: '1'\nworkflows: [{id: x, file: workflows/x.yaml}]\nskills: {required: [123]}\n")],
    ["invalid compat metadata", Buffer.from("id: root-pack\nversion: '1'\nworkflows: [{id: x, file: workflows/x.yaml}]\ncompat: {schemaVersion: 0}\n")],
    ["unknown YAML tag", Buffer.from("id: !mystery root-pack\nversion: '1.0.0'\nworkflows: [x]\n")],
    ["duplicate identity", Buffer.from("id: one\nid: two\nworkflows: [x]\n")],
    ["binary NUL", Buffer.from("id: root-pack\n\u0000workflows: [x]\n")],
    ["invalid UTF-8", Buffer.from([0x69, 0x64, 0x3a, 0x20, 0xff, 0x0a])],
    ["oversized", Buffer.from(`id: root-pack\n# ${"x".repeat(1024 * 1024)}\n`)],
  ])("refuses %s candidate even if the target manifest is valid", async (_name, bad) => {
    const rootFile = path.join(repo, "workflow.pack.yaml"); writeFileSync(rootFile, bad);
    put("packs/pack/workflow.pack.yaml", targetManifest); put("packs/pack/workflows/flow.yaml", "id: flow\nnodes: []\n");
    await expect(writeWorkflowSpecToRepo(repo, "pack", "flow", spec)).rejects.toThrow(/invalid|identity|size/i);
    await expect(ensurePackManifest(repo, "pack", "flow")).rejects.toThrow(/invalid|identity|size/i);
    expect(readFileSync(rootFile)).toEqual(bad);
    expect(readFileSync(path.join(repo, "packs/pack/workflow.pack.yaml"), "utf8")).toBe(targetManifest);
    expect(readFileSync(path.join(repo, "packs/pack/workflows/flow.yaml"), "utf8")).toBe("id: flow\nnodes: []\n");
  });
  it("accepts a different Pack with numeric identity and version normalized by TaskGuard", async () => {
    const other = "id: 42\nversion: 2\nworkflows:\n  - {id: 7, file: workflows/other.yaml}\n";
    expect(normalizePackManifest(parse(other))).toMatchObject({ id: "42", version: "2" });
    put("workflow.pack.yaml", other); put("packs/pack/workflow.pack.yaml", targetManifest);
    put("packs/pack/workflows/flow.yaml", "id: flow\nnodes: []\n");
    await writeWorkflowSpecToRepo(repo, "pack", "flow", spec); await ensurePackManifest(repo, "pack", "flow");
    expect(readFileSync(path.join(repo, "workflow.pack.yaml"), "utf8")).toBe(other);
  });
  it("detects duplicate target identities even alongside a different root pack", async () => {
    put("workflow.pack.yaml", rootManifest); put("pack/workflow.pack.yaml", targetManifest);
    put("packs/pack/workflow.pack.yaml", targetManifest);
    await expect(writeWorkflowSpecToRepo(repo, "pack", "flow", spec)).rejects.toThrow(/conflict/i);
    await expect(ensurePackManifest(repo, "pack", "flow")).rejects.toThrow(/conflict/i);
    expect(readFileSync(path.join(repo, "workflow.pack.yaml"), "utf8")).toBe(rootManifest);
    expect(readFileSync(path.join(repo, "pack/workflow.pack.yaml"), "utf8")).toBe(targetManifest);
    expect(readFileSync(path.join(repo, "packs/pack/workflow.pack.yaml"), "utf8")).toBe(targetManifest);
  });
  it("rejects a symlinked non-target manifest without following or modifying it", async () => {
    put("elsewhere.yaml", rootManifest); symlinkSync("elsewhere.yaml", path.join(repo, "workflow.pack.yaml"));
    put("packs/pack/workflow.pack.yaml", targetManifest); put("packs/pack/workflows/flow.yaml", "id: flow\nnodes: []\n");
    await expect(writeWorkflowSpecToRepo(repo, "pack", "flow", spec)).rejects.toThrow();
    await expect(ensurePackManifest(repo, "pack", "flow")).rejects.toThrow();
    expect(readFileSync(path.join(repo, "elsewhere.yaml"), "utf8")).toBe(rootManifest);
    expect(readFileSync(path.join(repo, "packs/pack/workflows/flow.yaml"), "utf8")).toBe("id: flow\nnodes: []\n");
  });

  it("rejects the same workflow ID in a different root Pack without changing either file", async () => {
    const other = "id: root-pack\nversion: '1.0.0'\nworkflows:\n  - {id: flow, file: workflows/flow.yaml}\n";
    put("workflow.pack.yaml", other); put("workflows/flow.yaml", "id: flow\ntitle: Root member\nnodes: []\n");
    put("packs/pack/workflow.pack.yaml", targetManifest); put("packs/pack/workflows/flow.yaml", "id: flow\ntitle: Target\nnodes: []\n");
    await expect(writeWorkflowSpecToRepo(repo, "pack", "flow", spec)).rejects.toThrow(/workflow|conflict|ambig/i);
    await expect(ensurePackManifest(repo, "pack", "flow")).rejects.toThrow(/workflow|conflict|ambig/i);
    expect(readFileSync(path.join(repo, "workflow.pack.yaml"), "utf8")).toBe(other);
    expect(readFileSync(path.join(repo, "workflows/flow.yaml"), "utf8")).toContain("Root member");
    expect(readFileSync(path.join(repo, "packs/pack/workflows/flow.yaml"), "utf8")).toContain("Target");
  });

  it("accepts a valid different Pack with full TaskGuard metadata", async () => {
    const other = "id: root-pack\nversion: '1'\ntitle: Root\nworkflows: [{id: x, file: workflows/x.yaml}]\nactions: [{id: act, type: shell, root: bin, commands: {run: run.sh}}]\nfacades: [{command: xx, defaultWorkflow: x, aliases: [alias], help: {summary: test, examples: [one]}}]\nskills: {required: [workflows]}\ncompat: {schemaVersion: 1, minRuntimeVersion: '1'}\n";
    expect(normalizePackManifest(parse(other))).toMatchObject({ id: "root-pack" });
    put("workflow.pack.yaml", other); put("packs/pack/workflow.pack.yaml", targetManifest); put("packs/pack/workflows/flow.yaml", "id: flow\nnodes: []\n");
    await writeWorkflowSpecToRepo(repo, "pack", "flow", spec); await ensurePackManifest(repo, "pack", "flow");
    expect(readFileSync(path.join(repo, "workflow.pack.yaml"), "utf8")).toBe(other);
  });

  it.each([
    ["workflow", "workflows: [{id: root-flow, file: packs/pack/workflows/flow.yaml}]\n"],
    ["action root", "workflows: [{id: root-flow, file: workflows/root.yaml}]\nactions: [{id: act, type: shell, root: packs/pack/workflows}]\n"],
    ["manifest as other workflow", "workflows: [{id: root-flow, file: packs/pack/workflow.pack.yaml}]\n"],
  ])("rejects when the different root Pack owns the target %s path", async (_kind, extra) => {
    const other = `id: root-pack\nversion: '1.0.0'\n${extra}`;
    put("workflow.pack.yaml", other); put("packs/pack/workflow.pack.yaml", targetManifest);
    put("packs/pack/workflows/flow.yaml", "id: flow\nnodes: []\n");
    await expect(writeWorkflowSpecToRepo(repo, "pack", "flow", spec)).rejects.toThrow(/conflict|resource|shared/i);
    await expect(ensurePackManifest(repo, "pack", "flow")).rejects.toThrow(/conflict|resource|shared/i);
    expect(readFileSync(path.join(repo, "workflow.pack.yaml"), "utf8")).toBe(other);
    expect(readFileSync(path.join(repo, "packs/pack/workflow.pack.yaml"), "utf8")).toBe(targetManifest);
    expect(readFileSync(path.join(repo, "packs/pack/workflows/flow.yaml"), "utf8")).toBe("id: flow\nnodes: []\n");
  });

  it.each(["workflow", "action root", "new workflow in action root"])("checks filesystem case aliases for a different Pack's %s", async kind => {
    const targetFile = path.join(repo, "packs/pack/workflows/flow.yaml");
    const isNew = kind === "new workflow in action root";
    put("packs/pack/workflow.pack.yaml", isNew
      ? "id: pack\nversion: '1.0.0'\nworkflows: [{id: other, file: workflows/other.yaml}]\n" : targetManifest);
    put(isNew ? "packs/pack/workflows/other.yaml" : "packs/pack/workflows/flow.yaml", isNew ? "id: other\nnodes: []\n" : "id: flow\nnodes: []\n");
    const other = kind === "workflow"
      ? "id: root-pack\nversion: '1.0.0'\nworkflows: [{id: root-flow, file: PACKS/pack/workflows/flow.yaml}]\n"
      : "id: root-pack\nversion: '1.0.0'\nworkflows: [{id: root-flow, file: workflows/root.yaml}]\nactions: [{id: act, type: shell, root: PACKS/pack/workflows}]\n";
    put("workflow.pack.yaml", other);
    const aliases = existsSync(path.join(repo, "PACKS/pack/workflows"));
    if (aliases) {
      await expect(writeWorkflowSpecToRepo(repo, "pack", "flow", spec)).rejects.toThrow(/conflict|shared/i);
      await expect(ensurePackManifest(repo, "pack", "flow")).rejects.toThrow(/conflict|shared/i);
      expect(existsSync(targetFile)).toBe(!isNew);
      if (!isNew) expect(readFileSync(targetFile, "utf8")).toBe("id: flow\nnodes: []\n");
    } else {
      await writeWorkflowSpecToRepo(repo, "pack", "flow", spec); await ensurePackManifest(repo, "pack", "flow");
      expect(readFileSync(targetFile, "utf8")).toContain("Updated");
    }
    expect(readFileSync(path.join(repo, "workflow.pack.yaml"), "utf8")).toBe(other);
  });

});


describe("reader-wide Pack ownership and workflow ID uniqueness", () => {
  const rootManifest = "id: pack\nversion: '1.0.0'\nworkflows: [{id: flow, file: workflows/flow.yaml}]\n";
  const rootSpec = "id: flow\ntitle: Original\nnodes: []\n";
  function seed() { runGit(repo, ["add", "."]); runGit(repo, ["commit", "-m", "pack fixtures"]); runGit(repo, ["push", bare, "HEAD:refs/heads/wf/flow"]); }
  it.each([
    ["save", "other"], ["deploy", "other"],
    ["save", "packs/other"], ["deploy", "packs/other"],
    ["save", "packs"], ["deploy", "packs"],
  ])("checks duplicate workflow ID during %s when another manifest is in %s", async (operation, directory) => {
    const otherId = path.basename(directory);
    const otherManifest = `id: ${otherId}\nversion: '1.0.0'\nworkflows: [{id: flow, file: workflows/flow.yaml}]\n`;
    put("workflow.pack.yaml", rootManifest); put("workflows/flow.yaml", rootSpec);
    put(`${directory}/workflow.pack.yaml`, otherManifest); put(`${directory}/workflows/flow.yaml`, "id: flow\ntitle: Other\nnodes: []\n"); seed();
    const base = runGit(root, ["--git-dir", bare, "rev-parse", "refs/heads/wf/flow"]);
    const work = await clone(); await prepareBranchBase(work, "wf/flow", "wf/flow");
    const ordinary = directory === "packs" || directory.startsWith("packs/");
    if (ordinary) {
      const snapshot = await readPackScriptSnapshot(work, "flow", "pack", "draft", { includeVersionFiles: true });
      expect(snapshot.packPath).toBe("");
      await writeWorkflowSpecToRepo(work, "pack", "flow", spec);
      await ensurePackManifest(work, "pack", "flow");
      expect(readFileSync(path.join(work, "workflows/flow.yaml"), "utf8")).toContain("Updated");
    } else {
      await expect(readPackScriptSnapshot(work, "flow", "pack", "draft")).rejects.toThrow(/conflict|ambig/i);
      await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/workflow|conflict|ambig/i);
      await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/workflow|conflict|ambig/i);
    }
    ordinary ? expect(readFileSync(path.join(work, "workflows/flow.yaml"), "utf8")).toContain("Updated")
      : expect(readFileSync(path.join(work, "workflows/flow.yaml"), "utf8")).toBe(rootSpec);
    expect(readFileSync(path.join(work, directory, "workflow.pack.yaml"), "utf8")).toBe(otherManifest);
    expect(runGit(root, ["--git-dir", bare, "rev-parse", "refs/heads/wf/flow"])).toBe(base);
    expect(runGit(root, ["--git-dir", bare, "for-each-ref", "--format=%(refname)", "refs/heads/saved/flow", "refs/tags/deploy/flow/"])).toBe("");
  });
  it.each([
    ["save", "other", "action"], ["deploy", "other", "action"], ["save", "packs/other", "action"], ["deploy", "packs/other", "action"],
    ["save", "other", "workflow"], ["deploy", "other", "workflow"], ["save", "packs/other", "workflow"], ["deploy", "packs/other", "workflow"],
  ])("checks the %s target owned by an unrelated Pack at %s (%s)", async (operation, directory, kind) => {
    const otherId = path.basename(directory); const target = `${directory}/shared/flow.yaml`;
    const targetManifest = `id: pack\nversion: '1.0.0'\nworkflows: [{id: flow, file: ${target}}]\n`;
    const otherManifest = kind === "workflow"
      ? `id: ${otherId}\nversion: '1.0.0'\nworkflows: [{id: other-flow, file: shared/flow.yaml}]\n`
      : `id: ${otherId}\nversion: '1.0.0'\nworkflows: [{id: other-flow, file: workflows/other.yaml}]\nactions: [{id: act, type: shell, root: shared}]\n`;
    put("workflow.pack.yaml", targetManifest); put(target, rootSpec);
    put(`${directory}/workflow.pack.yaml`, otherManifest); put(`${directory}/workflows/other.yaml`, "id: other-flow\nnodes: []\n"); seed();
    const base = runGit(root, ["--git-dir", bare, "rev-parse", "refs/heads/wf/flow"]);
    const work = await clone(); await prepareBranchBase(work, "wf/flow", "wf/flow");
    const ordinary = directory === "packs" || directory.startsWith("packs/");
    if (ordinary) {
      await writeWorkflowSpecToRepo(work, "pack", "flow", spec);
      await ensurePackManifest(work, "pack", "flow");
      expect(readFileSync(path.join(work, target), "utf8")).toContain("Updated");
    } else {
      await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/resource|conflict|shared/i);
      await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/resource|conflict|shared/i);
    }
    ordinary ? expect(readFileSync(path.join(work, target), "utf8")).toContain("Updated")
      : expect(readFileSync(path.join(work, target), "utf8")).toBe(rootSpec);
    expect(readFileSync(path.join(work, directory, "workflow.pack.yaml"), "utf8")).toBe(otherManifest);
    expect(runGit(root, ["--git-dir", bare, "rev-parse", "refs/heads/wf/flow"])).toBe(base);
    expect(runGit(root, ["--git-dir", bare, "for-each-ref", "--format=%(refname)", "refs/heads/saved/flow", "refs/tags/deploy/flow/"])).toBe("");
  });
  it.each([
    ["save", "other", false], ["deploy", "other", false], ["save", "packs/other", false], ["deploy", "packs/other", false],
    ["save", "other", true], ["deploy", "other", true], ["save", "packs/other", true], ["deploy", "packs/other", true],
  ])("checks %s when the workflow is another Pack manifest at %s (alias=%s)", async (operation, directory, useAlias) => {
    // This is both a valid Pack manifest and a workflow with id flow.
    const otherManifest = "id: flow\nversion: '1.0.0'\nworkflows: [{id: other-flow, file: workflows/other.yaml}]\nnodes: []\n";
    put(`${directory}/workflow.pack.yaml`, otherManifest);
    const alias = `${directory}/WORKFLOW.PACK.YAML`;
    const target = useAlias && existsSync(path.join(repo, alias)) ? alias : `${directory}/workflow.pack.yaml`;
    const targetManifest = `id: pack\nversion: '1.0.0'\nworkflows: [{id: flow, file: ${target}}]\n`;
    put("workflow.pack.yaml", targetManifest); seed();
    const refs = runGit(root, ["--git-dir", bare, "for-each-ref", "--format=%(refname) %(objectname)"]);
    const base = runGit(root, ["--git-dir", bare, "rev-parse", "refs/heads/wf/flow"]);
    const work = await clone(); await prepareBranchBase(work, "wf/flow", "wf/flow");
    const ordinary = directory === "packs" || directory.startsWith("packs/");
    if (ordinary) {
      await writeWorkflowSpecToRepo(work, "pack", "flow", spec);
      await ensurePackManifest(work, "pack", "flow");
      expect(readFileSync(path.join(work, target), "utf8")).toContain("Updated");
    } else {
      await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/resource|manifest|conflict/i);
      await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/resource|manifest|conflict/i);
    }
    if (!ordinary) expect(readFileSync(path.join(work, directory, "workflow.pack.yaml"), "utf8")).toBe(otherManifest);
    expect(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8")).toBe(targetManifest);
    expect(runGit(work, ["status", "--porcelain"])).toBe(ordinary ? `M ${directory}/workflow.pack.yaml` : "");
    expect(runGit(root, ["--git-dir", bare, "for-each-ref", "--format=%(refname) %(objectname)"])).toBe(refs);
    expect(runGit(root, ["--git-dir", bare, "show", `${base}:${directory}/workflow.pack.yaml`])).toBe(otherManifest.trimEnd());
  });
  it.each([["save", "other"], ["deploy", "packs/other"]])("keeps an independent %s Pack at %s and produces a readable snapshot", async (operation, directory) => {
    const otherManifest = "id: other\nversion: '1.0.0'\nworkflows: [{id: other-flow, file: workflows/other.yaml}]\n";
    put("workflow.pack.yaml", rootManifest); put("workflows/flow.yaml", rootSpec); put("scripts/run.sh", "echo keep\n");
    put(`${directory}/workflow.pack.yaml`, otherManifest); put(`${directory}/workflows/other.yaml`, "id: other-flow\nnodes: []\n"); seed();
    const work = await clone(); await prepareBranchBase(work, "wf/flow", "wf/flow");
    await writeWorkflowSpecToRepo(work, "pack", "flow", spec); await ensurePackManifest(work, "pack", "flow");
    if (operation === "save") await publishManifestFixture(work, "flow", "save", author); else await commitAndPushDeploy(work, "flow", 1, 1, "deploy", author);
    const ref = operation === "save" ? "wf/flow" : "deploy/flow/#1";
    const read = (file: string) => execFileSync("git", ["--git-dir", bare, "show", `${ref}:${file}`], { cwd: root, env: gitEnvironment() });
    expect(read(`${directory}/workflow.pack.yaml`)).toEqual(Buffer.from(otherManifest));
    expect(read(`${directory}/workflows/other.yaml`)).toEqual(Buffer.from("id: other-flow\nnodes: []\n"));
    const verify = await clone(); const snapshot = await readFixtureVersion(verify, "flow", "pack", operation === "save" ? "draft" : "current");
    expect(snapshot.files[0].content).toBe("echo keep\n");
  });
  it("fails closed on a malformed manifest in an unrelated directory", async () => {
    put("workflow.pack.yaml", rootManifest); put("workflows/flow.yaml", rootSpec); put("stranger/workflow.pack.yaml", "id: [\n"); seed();
    const work = await clone(); await prepareBranchBase(work, "wf/flow");
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/manifest/i);
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/manifest/i);
    expect(readFileSync(path.join(work, "workflows/flow.yaml"), "utf8")).toBe(rootSpec);
    expect(readFileSync(path.join(work, "stranger/workflow.pack.yaml"), "utf8")).toBe("id: [\n");
  });
  it.each(["untracked", "ignored"])("does not miss a conflicting %s manifest", async state => {
    put("workflow.pack.yaml", rootManifest); put("workflows/flow.yaml", rootSpec); seed();
    const work = await clone(); await prepareBranchBase(work, "wf/flow");
    if (state === "ignored") writeFileSync(path.join(work, ".git/info/exclude"), "other/\n");
    mkdirSync(path.join(work, "other"));
    const other = "id: other\nversion: '1.0.0'\nworkflows: [{id: flow, file: workflows/flow.yaml}]\n";
    writeFileSync(path.join(work, "other/workflow.pack.yaml"), other);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/workflow|conflict|ambig/i);
    expect(readFileSync(path.join(work, "workflows/flow.yaml"), "utf8")).toBe(rootSpec);
    expect(readFileSync(path.join(work, "other/workflow.pack.yaml"), "utf8")).toBe(other);
  });
  it("fails closed when an indexed other manifest is absent from the checkout", async () => {
    put("workflow.pack.yaml", rootManifest); put("workflows/flow.yaml", rootSpec);
    put("other/workflow.pack.yaml", "id: other\nversion: '1.0.0'\nworkflows: [{id: flow, file: workflows/flow.yaml}]\n"); seed();
    const work = await clone(); await prepareBranchBase(work, "wf/flow");
    runGit(work, ["update-index", "--skip-worktree", "other/workflow.pack.yaml"]);
    rmSync(path.join(work, "other/workflow.pack.yaml"));
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/manifest|discovery/i);
    expect(readFileSync(path.join(work, "workflows/flow.yaml"), "utf8")).toBe(rootSpec);
  });
  it("fails closed when candidate directory discovery exceeds the reader limit", async () => {
    const oid = runGit(repo, ["hash-object", "-w", "--stdin"], "x");
    const entries = Array.from({ length: 4097 }, (_, i) => `100644 ${oid}\tdir${String(i).padStart(4, "0")}/file.txt\n`).join("");
    runGit(repo, ["update-index", "--index-info"], entries);
    await expect(writeWorkflowSpecToRepo(repo, "pack", "flow", spec)).rejects.toThrow(/limit|discovery/i);
    expect(existsSync(path.join(repo, "packs/pack/workflow.pack.yaml"))).toBe(false);
  });

  it.each(["pack", "packs/pack"])("does not write inside an untracked embedded Git repository at %s", async directory => {
    const work = await clone(); await prepareBranchBase(work, "wf/flow");
    const nested = path.join(work, directory); mkdirSync(nested, { recursive: true });
    runGit(nested, ["init"]); runGit(nested, ["config", "user.name", "Test"]); runGit(nested, ["config", "user.email", author.email]);
    writeFileSync(path.join(nested, "workflow.pack.yaml"), "id: pack\nversion: '1.0.0'\nworkflows: [{id: flow, file: workflows/flow.yaml}]\n");
    mkdirSync(path.join(nested, "workflows")); writeFileSync(path.join(nested, "workflows/flow.yaml"), rootSpec);
    runGit(nested, ["add", "."]); runGit(nested, ["commit", "-m", "embedded"]);
    expect(runGit(work, ["ls-files", "--others", "--exclude-standard"])).toContain(`${directory}/`);
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/gitlink|submodule|nested git/i);
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/gitlink|submodule|nested git/i);
    expect(readFileSync(path.join(nested, "workflows/flow.yaml"), "utf8")).toBe(rootSpec);
  });
  it("protects a gitlink reached by filesystem case aliases", async () => {
    const work = await clone(); await prepareBranchBase(work, "wf/flow");
    const oid = runGit(repo, ["rev-parse", "HEAD"]);
    runGit(work, ["update-index", "--add", "--cacheinfo", `160000,${oid},PACKS/pack`]);
    mkdirSync(path.join(work, "PACKS/pack"), { recursive: true });
    if (existsSync(path.join(work, "packs/pack"))) {
      await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/gitlink|submodule/i);
      await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/gitlink|submodule/i);
      expect(existsSync(path.join(work, "PACKS/pack/workflow.pack.yaml"))).toBe(false);
    } else {
      await writeWorkflowSpecToRepo(work, "pack", "flow", spec); await ensurePackManifest(work, "pack", "flow");
      expect(existsSync(path.join(work, "packs/pack/workflow.pack.yaml"))).toBe(true);
    }
  });

  it("rejects a numeric workflow ID that normalizes to another Pack's string ID", async () => {
    const root = "id: pack\nversion: '1.0.0'\nworkflows: [{id: '42', file: workflows/42.yaml}]\n";
    const other = "id: other\nversion: '1.0.0'\nworkflows: [{id: 42, file: workflows/other.yaml}]\n";
    put("workflow.pack.yaml", root); put("workflows/42.yaml", "id: '42'\nnodes: []\n");
    put("other/workflow.pack.yaml", other); seed();
    const work = await clone(); await prepareBranchBase(work, "wf/42", "wf/flow");
    await expect(writeWorkflowSpecToRepo(work, "pack", "42", { ...spec, id: "42" })).rejects.toThrow(/workflow|conflict/i);
    await expect(ensurePackManifest(work, "pack", "42")).rejects.toThrow(/workflow|conflict/i);
    expect(readFileSync(path.join(work, "workflow.pack.yaml"), "utf8")).toBe(root);
    expect(readFileSync(path.join(work, "other/workflow.pack.yaml"), "utf8")).toBe(other);
  });
  it("refuses a target manifest in a directory whose name conflicts with its Pack ID", async () => {
    const manifest = "id: pack\nversion: '1.0.0'\nworkflows: [{id: flow, file: workflows/flow.yaml}]\n";
    put("moved/workflow.pack.yaml", manifest); put("moved/workflows/flow.yaml", rootSpec); seed();
    const work = await clone(); await prepareBranchBase(work, "wf/flow");
    await expect(writeWorkflowSpecToRepo(work, "pack", "flow", spec)).rejects.toThrow(/location|conflict/i);
    await expect(ensurePackManifest(work, "pack", "flow")).rejects.toThrow(/location|conflict/i);
    expect(readFileSync(path.join(work, "moved/workflow.pack.yaml"), "utf8")).toBe(manifest);
    expect(readFileSync(path.join(work, "moved/workflows/flow.yaml"), "utf8")).toBe(rootSpec);
  });

});
