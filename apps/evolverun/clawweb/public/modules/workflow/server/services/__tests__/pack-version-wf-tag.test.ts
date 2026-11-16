// @vitest-environment node
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { readPackVersionFromRemote } from '../pack-version-remote.js';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';

let root: string, remote: string, seed: string, published: string, draft: string;
const spec = (title: string) => JSON.stringify({ id: 'flow', title, version: '1', nodes: [{ id: 'step', executor: { type: 'agent' } }] });
const git = (...args: string[]) => runGit(seed, args);
const refs = () => runGit(root, ['--git-dir', remote, 'show-ref']);
const row = () => ({ workflow_id: 'flow', pack_id: 'pack', is_active: 1, version: 1, deploy_number: 1,
  tag_name: 'deploy/flow/#1', action: 'deploy', spec_json: spec('Published') });
const input = (version: 'current' | 'draft', active: unknown[] = [row()]) => ({ workflowId: 'flow', packId: 'pack', version,
  db: { query: async (sql: string) => sql.includes('workflow_deploy_history') ? active : [] } as never,
  gitConfig: { remoteUrl: remote, username: 'fixture', email: 'fixture@example.invalid' },
  gitAuthor: { name: 'Fixture', email: 'fixture@example.invalid' } });

beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'wf-tag-read-')); remote = createLocalRemote(root); seed = path.join(root, 'seed');
  mkdirSync(path.join(seed, 'workflows')); mkdirSync(path.join(seed, 'scripts'));
  writeFileSync(path.join(seed, 'workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
  writeFileSync(path.join(seed, 'workflows/flow.yaml'), spec('Published'));
  writeFileSync(path.join(seed, 'scripts/run.py'), 'print("published")\n');
  git('add', '.'); git('commit', '-m', 'release'); published = git('rev-parse', 'HEAD');
  git('tag', '-a', 'deploy/flow/#1', '-m', 'version=1'); git('push', remote, 'refs/tags/deploy/flow/#1');
  writeFileSync(path.join(seed, 'workflows/flow.yaml'), spec('Draft'));
  writeFileSync(path.join(seed, 'scripts/run.py'), 'print("draft")\n');
  git('add', '.'); git('commit', '-m', 'later draft'); draft = git('rev-parse', 'HEAD');
  git('push', remote, 'HEAD:refs/heads/wf/flow');
});
afterEach(() => rmSync(root, { recursive: true, force: true, maxRetries: 5 }));

it('reads wf as draft without a saved branch and reports its wf write lease', async () => {
  const before = refs();
  const snapshot = await readPackVersionFromRemote(input('draft'));
  expect(snapshot).toMatchObject({ baseRef: 'origin/wf/flow', baseCommit: draft, draftHead: draft });
  expect(snapshot.versionFiles).toContainEqual({ path: 'scripts/run.py', mode: '100644', contentBase64: Buffer.from('print("draft")\n').toString('base64') });
  expect(refs()).toBe(before);
});
it('reads the active tag as production even when wf contains a newer unpublished draft', async () => {
  const before = refs();
  const snapshot = await readPackVersionFromRemote(input('current'));
  expect(snapshot).toMatchObject({ baseRef: 'refs/tags/deploy/flow/#1', baseCommit: published, draftHead: draft });
  expect(snapshot.versionFiles).toContainEqual({ path: 'scripts/run.py', mode: '100644', contentBase64: Buffer.from('print("published")\n').toString('base64') });
  expect(refs()).toBe(before);
});
it('never presents an unpublished wf as production when no active deployment exists', async () => {
  const before = refs();
  await expect(readPackVersionFromRemote(input('current', []))).rejects.toThrow('Workflow snapshot not found');
  expect(refs()).toBe(before);
});
it('does not fall back to wf when the active deployment tag is missing', async () => {
  runGit(root, ['--git-dir', remote, 'update-ref', '-d', 'refs/tags/deploy/flow/#1']);
  const before = refs();
  await expect(readPackVersionFromRemote(input('current'))).rejects.toThrow();
  expect(refs()).toBe(before);
});
it('reads an existing active tag even if the mutable workflow branch is absent', async () => {
  runGit(root, ['--git-dir', remote, 'update-ref', '-d', 'refs/heads/wf/flow']);
  const snapshot = await readPackVersionFromRemote(input('current'));
  expect(snapshot).toMatchObject({ baseRef: 'refs/tags/deploy/flow/#1', baseCommit: published, draftHead: null });
});

it('ignores a newer saved branch when selecting the wf draft', async () => {
  writeFileSync(path.join(seed, 'scripts/run.py'), 'print("ignored saved")\n');
  git('add', '.'); git('commit', '-m', 'unused saved'); git('push', remote, 'HEAD:refs/heads/saved/flow');
  const before = refs();
  const snapshot = await readPackVersionFromRemote(input('draft'));
  expect(snapshot).toMatchObject({ baseRef: 'origin/wf/flow', baseCommit: draft, draftHead: draft });
  expect(snapshot.versionFiles).toContainEqual({ path: 'scripts/run.py', mode: '100644', contentBase64: Buffer.from('print("draft")\n').toString('base64') });
  expect(refs()).toBe(before);
});
it('does not fall back to saved when wf is absent, while the active tag stays readable', async () => {
  git('push', remote, 'HEAD:refs/heads/saved/flow');
  runGit(root, ['--git-dir', remote, 'update-ref', '-d', 'refs/heads/wf/flow']);
  const before = refs();
  await expect(readPackVersionFromRemote(input('draft'))).rejects.toThrow('Workflow snapshot not found');
  expect((await readPackVersionFromRemote(input('current'))).baseCommit).toBe(published);
  expect(refs()).toBe(before);
});

it('returns a complete draft inventory when its manifest-declared workflow YAML is malformed', async () => {
  const yaml = 'id: flow\ntitle: [broken\n';
  writeFileSync(path.join(seed, 'workflows/flow.yaml'), yaml);
  git('add', '.'); git('commit', '-m', 'malformed draft'); draft = git('rev-parse', 'HEAD');
  git('push', remote, 'HEAD:refs/heads/wf/flow');
  const snapshot = await readPackVersionFromRemote(input('draft'));
  expect(snapshot).toMatchObject({ baseCommit: draft, draftHead: draft, packId: 'pack' });
  expect(snapshot.versionFiles).toContainEqual({ path: 'workflows/flow.yaml', mode: '100644', contentBase64: Buffer.from(yaml).toString('base64') });
  expect(snapshot.versionFiles).toContainEqual({ path: 'scripts/run.py', mode: '100644', contentBase64: Buffer.from('print("draft")\n').toString('base64') });
});

it('continues to reject a draft whose parseable workflow ID conflicts with the manifest', async () => {
  writeFileSync(path.join(seed, 'workflows/flow.yaml'), 'id: another\nnodes: []\n');
  git('add', '.'); git('commit', '-m', 'wrong workflow'); git('push', remote, 'HEAD:refs/heads/wf/flow');
  await expect(readPackVersionFromRemote(input('draft'))).rejects.toThrow('Workflow identity mismatch');
});
