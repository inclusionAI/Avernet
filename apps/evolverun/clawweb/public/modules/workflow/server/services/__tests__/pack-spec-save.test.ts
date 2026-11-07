// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { parse as parseYaml } from 'yaml';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
import { withPreparedWorkflowSpec, type PrepareWorkflowSpecInput } from '../pack-spec-save.js';
import { resumePreparedPackCandidate, PackCandidateConflictError, PackCandidateOutcomeUnknownError } from '../pack-candidate-save.js';
import type { WorkflowSpec } from '../../workflow.js';

vi.setConfig({ testTimeout: 30_000, hookTimeout: 30_000 });
let root: string, remote: string, seed: string;
const git = (dir: string, ...args: string[]) => runGit(dir, args);
const remoteGit = (...args: string[]) => git(root, '--git-dir', remote, ...args);
const spec = { id: 'flow', title: 'Edited YAML', nodes: [] } as WorkflowSpec;
const author = { name: 'Spec Author', email: 'author@example.invalid' };
function input(extra: Partial<PrepareWorkflowSpecInput> = {}): PrepareWorkflowSpecInput {
  return { workflowId: 'flow', packId: 'pack', spec, author,
    config: { remoteUrl: remote, username: 'Transport User', email: 'transport@example.invalid' }, ...extra };
}
function seedPack(prefix = 'packs/pack') {
  mkdirSync(path.join(seed, prefix, 'workflows'), { recursive: true });
  writeFileSync(path.join(seed, prefix, 'workflow.pack.yaml'), '# Shared pack metadata\nid: pack\nversion: 4\nworkflows:\n  - id: flow\n    file: workflows/custom.yaml\n  - id: sibling\n    file: workflows/sibling.yaml\n');
  writeFileSync(path.join(seed, prefix, 'workflows/custom.yaml'), 'id: flow\ntitle: Original\nnodes: []\n');
  writeFileSync(path.join(seed, prefix, 'workflows/sibling.yaml'), 'id: sibling\ntitle: Sibling\nnodes: []\n');
  writeFileSync(path.join(seed, prefix, 'asset.bin'), Buffer.from([0, 255, 128, 10]));
  writeFileSync(path.join(seed, prefix, 'run.sh'), '#!/bin/sh\necho run\n');
  git(seed, 'add', '.'); git(seed, 'update-index', '--chmod=+x', `${prefix}/run.sh`);
  git(seed, 'commit', '-m', 'full pack'); return git(seed, 'rev-parse', 'HEAD');
}
beforeEach(() => { root = mkdtempSync(path.join(tmpdir(), 'pack-spec-save-')); remote = createLocalRemote(root); seed = path.join(root, 'seed'); });
afterEach(() => { vi.restoreAllMocks(); rmSync(root, { recursive: true, force: true }); });

describe('prepared legacy workflow-spec save', () => {
  it('prepares a new workflow using default HEAD, then pushes saved only after registration', async () => {
    const base = git(seed, 'rev-parse', 'HEAD');
    const before = remoteGit('show-ref');
    const prepared = await withPreparedWorkflowSpec(input(), async (p, push) => {
      expect(remoteGit('show-ref')).toBe(before);
      expect(p).toMatchObject({ workflowId: 'flow', packId: 'pack', workflowFile: 'workflows/flow.yaml',
        packPath: 'packs/pack/', sourceCommit: base, sourceVersion: 'initial', expectedSavedHead: null });
      expect(parseYaml(p.content)).toMatchObject({ id: 'flow', title: 'Edited YAML' });
      expect(parseYaml(p.manifestContent)).toMatchObject({ id: 'pack', workflows: [{ id: 'flow', file: 'workflows/flow.yaml' }] });
      await push(); return p;
    });
    expect(remoteGit('rev-parse', 'refs/heads/saved/flow')).toBe(prepared.commit);
    expect(remoteGit('show', '-s', '--format=%an <%ae>', prepared.commit)).toBe('Spec Author <author@example.invalid>');
    expect(remoteGit('branch', '--list', 'wf/flow')).toBe('');
    expect(remoteGit('tag', '-l')).toBe('');
  });

  it('supports an unborn remote, root commit and exact recovery of the first save', async () => {
    remote = path.join(root, 'empty.git'); git(root, 'init', '--bare', '--initial-branch=main', remote);
    const prepared = await withPreparedWorkflowSpec(input(), async p => p);
    expect(prepared.sourceCommit).toBeNull();
    expect(prepared.expectedSavedHead).toBeNull();
    expect(prepared.sourceVersion).toBe('initial');
    expect(await resumePreparedPackCandidate(JSON.parse(JSON.stringify(prepared)), input().config)).toMatchObject({ commit: prepared.commit });
    expect(remoteGit('show', '-s', '--format=%P', prepared.commit)).toBe('');
    expect(remoteGit('show-ref')).toBe(`${prepared.commit} refs/heads/saved/flow`);
  });

  it.each(['wf', 'saved'])('inherits all resources and declared YAML path from %s rather than default HEAD', async branch => {
    const base = seedPack(); git(seed, 'push', remote, `${base}:refs/heads/${branch}/flow`);
    const prepared = await withPreparedWorkflowSpec(input({ sourceCommit: base, expectedSavedHead: branch === 'saved' ? base : null }),
      async (p, push) => { await push(); return p; });
    expect(prepared.sourceCommit).toBe(base);
    expect(prepared.workflowFile).toBe('workflows/custom.yaml');
    expect(prepared.manifestContent).toBe(remoteGit('show', `${base}:packs/pack/workflow.pack.yaml`) + '\n');
    for (const file of ['packs/pack/asset.bin', 'packs/pack/run.sh', 'packs/pack/workflows/sibling.yaml']) {
      expect(remoteGit('ls-tree', prepared.commit, file)).toBe(remoteGit('ls-tree', base, file));
    }
    expect(execFileSync('git', ['--git-dir', remote, 'show', `${prepared.commit}:packs/pack/asset.bin`])).toEqual(Buffer.from([0, 255, 128, 10]));
    expect(remoteGit('ls-tree', prepared.commit, 'packs/pack/run.sh')).toMatch(/^100755 blob/);
    expect(remoteGit('tag', '-l')).toBe('');
    expect(remoteGit('branch', '--list', 'wf/flow')).toBe(branch === 'wf' ? 'wf/flow' : '');
  });

  it.each(['wf', 'saved'])('updates an engine-root Pack in place on %s without creating a nested copy', async branch => {
    const base = seedPack('.');
    git(seed, 'push', remote, `${base}:refs/heads/${branch}/flow`);
    const prepared = await withPreparedWorkflowSpec(input(), async (candidate, push) => {
      await push(); return candidate;
    });
    expect(prepared.packPath).toBe('');
    expect(prepared.workflowFile).toBe('workflows/custom.yaml');
    expect(remoteGit('ls-tree', '-r', '--name-only', prepared.commit).split('\n'))
      .toEqual(remoteGit('ls-tree', '-r', '--name-only', base).split('\n'));
    for (const file of ['workflow.pack.yaml', 'asset.bin', 'run.sh', 'workflows/sibling.yaml']) {
      expect(remoteGit('ls-tree', prepared.commit, file)).toBe(remoteGit('ls-tree', base, file));
    }
    expect(remoteGit('ls-tree', prepared.commit, 'run.sh')).toMatch(/^100755 blob/);
    expect(parseYaml(remoteGit('show', `${prepared.commit}:workflows/custom.yaml`))).toMatchObject(spec);
    expect(remoteGit('ls-tree', '-r', '--name-only', prepared.commit)).not.toContain('packs/pack/');
  });

  it('refuses an existing root/nested duplicate before preparing or publishing a replacement', async () => {
    seedPack('.');
    mkdirSync(path.join(seed, 'packs/pack/workflows'), { recursive: true });
    writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
    writeFileSync(path.join(seed, 'packs/pack/workflows/flow.yaml'), 'id: flow\ntitle: Different historical version\nnodes: []\n');
    git(seed, 'add', '.'); git(seed, 'update-index', '--chmod=+x', 'run.sh');
    git(seed, 'commit', '-m', 'legacy duplicate');
    const base = git(seed, 'rev-parse', 'HEAD');
    git(seed, 'push', remote, `${base}:refs/heads/saved/flow`, `${base}:refs/heads/wf/flow`);
    const before = remoteGit('show-ref');
    let prepared = false;
    await expect(withPreparedWorkflowSpec(input(), async (_candidate, push) => {
      prepared = true; await push();
    })).rejects.toThrow(/conflict/i);
    expect(prepared).toBe(false);
    expect(remoteGit('show-ref')).toBe(before);
    expect(remoteGit('ls-tree', '-r', '--name-only', base)).toContain('packs/pack/workflow.pack.yaml');
    expect(remoteGit('ls-tree', base, 'run.sh')).toMatch(/^100755 blob/);
  });

  it('prefers saved over wf and rejects either optional baseline when stale', async () => {
    const deployed = seedPack(); git(seed, 'push', remote, `${deployed}:refs/heads/wf/flow`);
    writeFileSync(path.join(seed, 'packs/pack/draft-only'), 'keep draft'); git(seed, 'add', '.'); git(seed, 'commit', '-m', 'draft');
    const saved = git(seed, 'rev-parse', 'HEAD'); git(seed, 'push', remote, `${saved}:refs/heads/saved/flow`);
    await expect(withPreparedWorkflowSpec(input({ sourceCommit: deployed }), async (_p, push) => push())).rejects.toBeInstanceOf(PackCandidateConflictError);
    await expect(withPreparedWorkflowSpec(input({ expectedSavedHead: null }), async (_p, push) => push())).rejects.toBeInstanceOf(PackCandidateConflictError);
    const prepared = await withPreparedWorkflowSpec(input(), async (p, push) => { await push(); return p; });
    expect(prepared.expectedSavedHead).toBe(saved);
    expect(prepared.sourceVersion).toBe('draft');
    expect(remoteGit('show', `${prepared.commit}:packs/pack/draft-only`)).toBe('keep draft');
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(deployed);
  });

  it('keeps remote unchanged on registration failure and still CASes a first-save race', async () => {
    const before = remoteGit('show-ref');
    await expect(withPreparedWorkflowSpec(input(), async () => { throw new Error('journal unavailable'); })).rejects.toThrow('journal unavailable');
    expect(remoteGit('show-ref')).toBe(before);
    const first = await withPreparedWorkflowSpec(input(), async p => p);
    await expect(withPreparedWorkflowSpec(input({ spec: { ...spec, title: 'Other' } }), async (_prepared, push) => {
      await resumePreparedPackCandidate(first, input().config);
      return push();
    })).rejects.toBeInstanceOf(PackCandidateOutcomeUnknownError);
    expect(remoteGit('rev-parse', 'refs/heads/saved/flow')).toBe(first.commit);
  });

  it('does not regenerate after a push loses acknowledgement', async () => {
    const execute = gitExecution.executeGit; let pushes = 0;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      const result = await execute(args, options);
      if (args[0] === 'push') { pushes++; throw new Error('ack lost'); }
      return result;
    });
    let commit = '';
    await expect(withPreparedWorkflowSpec(input(), async (prepared, push) => { commit = prepared.commit; return push(); }))
      .rejects.toMatchObject({ name: 'PackCandidateOutcomeUnknownError' });
    expect(remoteGit('rev-parse', 'refs/heads/saved/flow')).toBe(commit);
    expect(pushes).toBe(1);
  });
  it('propagates a push failure even if the callback returns without awaiting it', async () => {
    const execute = gitExecution.executeGit;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push') throw new Error('connection lost');
      return execute(args, options);
    });
    await expect(withPreparedWorkflowSpec(input(), async (_prepared, push) => {
      void push().catch(() => {});
      return 'registered';
    })).rejects.toBeInstanceOf(PackCandidateOutcomeUnknownError);
  });

  it('preserves an unchanged manifest blob despite checkout EOL conversion', async () => {
    seedPack();
    writeFileSync(path.join(seed, '.gitattributes'), 'packs/pack/workflow.pack.yaml text eol=crlf\n');
    git(seed, 'add', '.gitattributes'); git(seed, 'commit', '-m', 'checkout conversion');
    const base = git(seed, 'rev-parse', 'HEAD'); git(seed, 'push', remote, `${base}:refs/heads/saved/flow`);
    const prepared = await withPreparedWorkflowSpec(input(), async (p, push) => { await push(); return p; });
    expect(remoteGit('ls-tree', prepared.commit, 'packs/pack/workflow.pack.yaml'))
      .toBe(remoteGit('ls-tree', base, 'packs/pack/workflow.pack.yaml'));
  });

  it('keeps reconciliation identity when the callback throws during an in-flight push', async () => {
    const execute = gitExecution.executeGit; let commit = '';
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      const result = await execute(args, options);
      if (args[0] === 'push') throw new Error('lost acknowledgement');
      return result;
    });
    await expect(withPreparedWorkflowSpec(input(), async (prepared, push) => {
      commit = prepared.commit; void push().catch(() => {}); throw new Error('callback post-registration failure');
    })).rejects.toMatchObject({ name: 'PackCandidateOutcomeUnknownError', branch: 'saved/flow' });
    expect(remoteGit('rev-parse', 'refs/heads/saved/flow')).toBe(commit);
  });

});
