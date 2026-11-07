// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
import { PackCandidateConflictError, PackCandidateOutcomeUnknownError, PackWorkflowContentChangedError, savePackCandidate,
  withPreparedPackCandidateWithWorkflow, type SavePackCandidateInput } from '../pack-candidate-save.js';

let root: string, bare: string, seed: string, current: string;
const b64 = (s: string) => Buffer.from(s).toString('base64');
const yaml = 'id: flow\nname: edited\n';
const show = (commit: string, file: string) => execFileSync('git', ['--git-dir', bare, 'show', `${commit}:${file}`]);
const ref = () => runGit(root, ['ls-remote', bare, 'refs/heads/wf/flow']);
function input(extra: Partial<SavePackCandidateInput> = {}): SavePackCandidateInput {
  return { workflowId: 'flow', sourceVersion: 'current', sourceCommit: current, expectedSavedHead: current,
    config: { remoteUrl: bare, username: 'test', email: 'test@example.invalid' }, changes: [], ...extra };
}
function prepare(value: SavePackCandidateInput, content = yaml) {
  return withPreparedPackCandidateWithWorkflow(value, content, async (prepared, push) => { await push(); return prepared; });
}
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'candidate-workflow-')); bare = createLocalRemote(root); seed = path.join(root, 'seed');
  mkdirSync(path.join(seed, 'packs/pack/workflows'), { recursive: true });
  mkdirSync(path.join(seed, 'packs/pack/scripts'), { recursive: true });
  writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow, other]\n');
  writeFileSync(path.join(seed, 'packs/pack/workflows/flow.yaml'), 'id: flow\n');
  chmodSync(path.join(seed, 'packs/pack/workflows/flow.yaml'), 0o755);
  writeFileSync(path.join(seed, 'packs/pack/workflows/other.yaml'), 'id: other\n');
  writeFileSync(path.join(seed, 'packs/pack/scripts/untouched.bin'), Buffer.from([0, 255, 128]));
  chmodSync(path.join(seed, 'packs/pack/scripts/untouched.bin'), 0o755);
  writeFileSync(path.join(seed, 'outside'), 'outside');
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'seed']);
  current = runGit(seed, ['rev-parse', 'HEAD']); runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`]);
});
afterEach(() => rmSync(root, { recursive: true, force: true }));

describe('trusted workflow and file candidate', () => {
  it('commits workflow and binary changes together, inheriting untouched objects and modes', async () => {
    const result = await prepare(input({ changes: [{ path: 'scripts/new.bin', operation: 'create',
      contentBase64: Buffer.from([255, 0, 42]).toString('base64'), mode: '100755' }] }));
    expect(result.content).toBe(yaml);
    expect(show(result.commit, 'packs/pack/workflows/flow.yaml').toString()).toBe(yaml);
    expect(show(result.commit, 'packs/pack/scripts/new.bin')).toEqual(Buffer.from([255, 0, 42]));
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', result.commit, 'packs/pack/workflows/flow.yaml'])).toMatch(/^100755 blob/);
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', result.commit, 'packs/pack/scripts/new.bin'])).toMatch(/^100755 blob/);
    for (const name of ['packs/pack/scripts/untouched.bin', 'packs/pack/workflows/other.yaml', 'outside']) {
      expect(runGit(root, ['--git-dir', bare, 'rev-parse', `${result.commit}:${name}`]))
        .toBe(runGit(root, ['--git-dir', bare, 'rev-parse', `${current}:${name}`]));
    }
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', result.commit, 'packs/pack/scripts/untouched.bin'])).toMatch(/^100755 blob/);
    expect(runGit(root, ['--git-dir', bare, 'rev-parse', `${result.commit}^`])).toBe(current);
    expect(ref()).toContain(result.commit);
  });
  it.each(['id: wrong\n', 'not: [valid\n', '[]\n'])('rejects invalid selected workflow %s', async content => {
    await expect(prepare(input(), content)).rejects.toThrow(); expect(ref()).toBe(`${current}\trefs/heads/wf/flow`);
  });
  it.each(['id: pack\nworkflows: [flow, missing]\n', 'id: pack\nworkflows: [flow, flow]\n', 'id: pack\nworkflows: [other]\n'])
  ('validates final manifest with trusted workflow content', async manifest => {
    await expect(prepare(input({ changes: [{ path: 'workflow.pack.yaml', operation: 'replace', contentBase64: b64(manifest), mode: '100644' }] }))).rejects.toThrow();
    expect(ref()).toBe(`${current}\trefs/heads/wf/flow`);
  });
  it('rejects a competing ordinary change to the selected workflow', async () => {
    await expect(prepare(input({ changes: [{ path: 'workflows/flow.yaml', operation: 'replace', contentBase64: b64('id: flow\nname: other\n'), mode: '100644' }] }))).rejects.toThrow();
    expect(ref()).toBe(`${current}\trefs/heads/wf/flow`);
  });
  it('does not allow legacy file API to opt out of workflow preservation through extra input', async () => {
    const legacy = { ...input({ preserveWorkflowContent: true, changes: [{ path: 'workflows/flow.yaml', operation: 'replace', contentBase64: b64(yaml), mode: '100644' }] }), workflowYaml: yaml };
    await expect(savePackCandidate(legacy)).rejects.toBeInstanceOf(PackWorkflowContentChangedError);
    expect(ref()).toBe(`${current}\trefs/heads/wf/flow`);
  });
  it('rejects a stale expected saved head without modifying it', async () => {
    const first = await prepare(input());
    await expect(prepare(input())).rejects.toBeInstanceOf(PackCandidateConflictError);
    expect(ref()).toContain(first.commit);
  });
  it('rejects an advanced saved head before invoking the callback', async () => {
    const competing = await withPreparedPackCandidateWithWorkflow(input(), yaml, async prepared => prepared);
    const winner = await prepare(input(), 'id: flow\nname: winner\n');
    let called = false;
    await expect(withPreparedPackCandidateWithWorkflow(input(), yaml, async (_prepared, push) => { called = true; return push(); }))
      .rejects.toBeInstanceOf(PackCandidateConflictError);
    expect(called).toBe(false); expect(ref()).toContain(winner.commit); expect(ref()).not.toContain(competing.commit);
  });
  it('writes the selected workflow at its final manifest path and supports an atomic move', async () => {
    const result = await prepare(input({ changes: [
      { path: 'workflows/flow.yaml', operation: 'delete' },
      { path: 'workflow.pack.yaml', operation: 'replace', mode: '100644', contentBase64: b64(
        'id: pack\nworkflows:\n  - id: flow\n    file: workflows/moved.yaml\n  - other\n') },
    ] }));
    expect(result.workflowFile).toBe('workflows/moved.yaml');
    expect(show(result.commit, 'packs/pack/workflows/moved.yaml').toString()).toBe(yaml);
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', result.commit, 'packs/pack/workflows/moved.yaml'])).toMatch(/^100755 blob/);
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', result.commit, 'packs/pack/workflows/flow.yaml'])).toBe('');
  });
  it.each([
    ['script', 'scripts/run.sh', '#!/bin/sh\necho do-not-overwrite\n', false],
    ['unreferenced YAML', 'workflows/unused.yaml', 'id: flow\nname: keep\n', false],
    ['script with Pack rename', 'scripts/run.sh', '#!/bin/sh\necho keep-after-rename\n', true],
    ['unreferenced YAML with Pack rename', 'workflows/unused.yaml', 'id: flow\nname: keep\n', true],
  ] as const)('rejects implicit overwrite of an existing %s before callback', async (_label, target, content, rename) => {
    const targetPath = `packs/pack/${target}`;
    writeFileSync(path.join(seed, targetPath), content);
    runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'existing target']);
    current = runGit(seed, ['rev-parse', 'HEAD']);
    runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`]);
    const beforeRefs = runGit(root, ['--git-dir', bare, 'show-ref']);
    let called = false;
    const manifest = `id: ${rename ? 'renamed' : 'pack'}\nworkflows:\n  - id: flow\n    file: ${target}\n  - other\n`;
    await expect(withPreparedPackCandidateWithWorkflow(input({ sourceVersion: 'draft', sourceCommit: current,
      expectedSavedHead: current, changes: [{ path: 'workflow.pack.yaml', operation: 'replace',
        contentBase64: b64(manifest), mode: '100644' }] }), yaml, async () => { called = true; }))
      .rejects.toThrow('Workflow target already exists');
    expect(called).toBe(false);
    expect(runGit(root, ['--git-dir', bare, 'show-ref'])).toBe(beforeRefs);
    expect(show(current, targetPath).toString()).toBe(content);
    expect(show(current, 'packs/pack/workflows/flow.yaml').toString()).toBe('id: flow\n');
    expect(show(current, 'packs/pack/workflow.pack.yaml').toString()).toBe('id: pack\nworkflows: [flow, other]\n');
  });
  it.each(['original workflow', 'new occupied target'])('does not restore a deleted %s implicitly', async kind => {
    runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`]);
    const beforeRefs = runGit(root, ['--git-dir', bare, 'show-ref']);
    const target = kind === 'original workflow' ? 'workflows/flow.yaml' : 'scripts/untouched.bin';
    const changes: SavePackCandidateInput['changes'] = [{ path: target, operation: 'delete' }];
    if (kind !== 'original workflow') changes.push({ path: 'workflow.pack.yaml', operation: 'replace', mode: '100644',
      contentBase64: b64(`id: pack\nworkflows:\n  - id: flow\n    file: ${target}\n  - other\n`) });
    let called = false;
    await expect(withPreparedPackCandidateWithWorkflow(input({ sourceVersion: 'draft', sourceCommit: current,
      expectedSavedHead: current, changes }), yaml, async () => { called = true; })).rejects.toThrow('Conflicting selected workflow change');
    expect(called).toBe(false);
    expect(runGit(root, ['--git-dir', bare, 'show-ref'])).toBe(beforeRefs);
    expect(show(current, `packs/pack/${target}`)).toEqual(kind === 'original workflow' ? Buffer.from('id: flow\n') : Buffer.from([0, 255, 128]));
  });
  it('permits the original workflow path when its Pack is renamed', async () => {
    const result = await prepare(input({ changes: [{ path: 'workflow.pack.yaml', operation: 'replace', mode: '100644',
      contentBase64: b64('id: renamed\nworkflows: [flow, other]\n') }] }));
    expect(result.packPath).toBe('packs/renamed/');
    expect(result.workflowFile).toBe('workflows/flow.yaml');
    expect(show(result.commit, 'packs/renamed/workflows/flow.yaml').toString()).toBe(yaml);
    expect(show(result.commit, 'packs/renamed/scripts/untouched.bin')).toEqual(Buffer.from([0, 255, 128]));
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', result.commit, 'packs/renamed/workflows/flow.yaml'])).toMatch(/^100755 blob/);
  });
  it('enforces the saved lease if another writer wins after preparation', async () => {
    let preparedCommit = '';
    await expect(withPreparedPackCandidateWithWorkflow(input(), yaml, async (prepared, push) => {
      preparedCommit = prepared.commit;
      const competing = runGit(seed, ['commit-tree', `${current}^{tree}`, '-p', current, '-m', 'competing wf writer']);
      runGit(seed, ['push', bare, `${competing}:refs/heads/wf/flow`]);
      return push();
    })).rejects.toBeInstanceOf(PackCandidateOutcomeUnknownError);
    expect(preparedCommit).not.toBe(current);
    expect(ref()).not.toContain(current);
    expect(ref()).not.toContain(preparedCommit);
  });
  it('rejects oversized workflow content before touching the wf branch', async () => {
    await expect(prepare(input(), 'x'.repeat(1024 * 1024 + 1))).rejects.toThrow('Invalid workflow content');
    expect(ref()).toBe(`${current}\trefs/heads/wf/flow`);
  });

});
