// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { readPackScriptSnapshot } from '../pack-script-snapshot.js';
let repo: string;
function git(...args: string[]): string { return execFileSync('git', args, { cwd: repo, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim(); }
function put(file: string, content: string | Buffer) { const target = path.join(repo, file); mkdirSync(path.dirname(target), { recursive: true }); writeFileSync(target, content); }
function commit(): string { git('add', '-A'); git('commit', '-qm', 'fixture'); return git('rev-parse', 'HEAD'); }
function ref(name: string, sha: string) { git('update-ref', `refs/remotes/origin/${name}/flow`, sha); }
beforeEach(() => { repo = mkdtempSync(path.join(tmpdir(), 'pack-snapshot-')); git('init', '-q'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid'); put('pack/workflow.pack.yaml', 'id: pack\nversion: 1\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n'); put('pack/workflows/flow.yaml', 'id: flow\nnodes: []\n'); put('pack/scripts/a.py', 'print(1)\r\n'); put('pack/scripts/sub/empty.sh', ''); commit(); });
afterEach(() => rmSync(repo, { recursive: true, force: true }));
describe('readPackScriptSnapshot', () => {
  it('reads identical refs preserving bytes, empty files and executable mode', async () => {
    git('update-index', '--chmod=+x', 'pack/scripts/sub/empty.sh'); git('commit', '-qm', 'executable'); const sha = git('rev-parse', 'HEAD'); ref('wf', sha); ref('saved', sha);
    expect(await readPackScriptSnapshot(repo, 'flow', 'pack')).toEqual({ workflowId: 'flow', packId: 'pack', baseCommit: sha, baseRef: 'origin/wf/flow', files: [{ path: 'scripts/a.py', content: 'print(1)\r\n', mode: '100644' }, { path: 'scripts/sub/empty.sh', content: '', mode: '100755' }] });
  });
  it('handles missing refs and rejects when both are absent', async () => { await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/not.found/i); const sha = git('rev-parse', 'HEAD'); ref('saved', sha); expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).baseRef).toBe('origin/saved/flow'); });
  it('chooses descendants in either direction and rejects divergence', async () => { const first = git('rev-parse', 'HEAD'); put('pack/scripts/a.py', 'new'); const second = commit(); ref('wf', first); ref('saved', second); expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).baseCommit).toBe(second); ref('wf', second); ref('saved', first); expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).baseRef).toBe('origin/wf/flow'); git('checkout', '-q', '--detach', first); put('pack/scripts/a.py', 'other'); const third = commit(); ref('saved', third); await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/conflict/i); });
  it('supports packs layout and rejects duplicate manifests', async () => { git('mv', 'pack', 'tmp-pack'); mkdirSync(path.join(repo, 'packs')); git('mv', 'tmp-pack', 'packs/pack'); ref('wf', commit()); expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).files[0].path).toBe('scripts/a.py'); put('pack/workflow.pack.yaml', 'duplicate'); ref('wf', commit()); await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/conflict/i); });
  it('reads a root pack from the committed ref, not the working tree', async () => {
    git('mv', 'pack/workflow.pack.yaml', 'workflow.pack.yaml');
    git('mv', 'pack/workflows', 'workflows');
    git('mv', 'pack/scripts', 'scripts');
    commit(); git('update-index', '--chmod=+x', 'scripts/sub/empty.sh');
    git('commit', '-qm', 'executable'); const sha = git('rev-parse', 'HEAD'); ref('wf', sha);
    put('scripts/a.py', 'uncommitted');
    expect(await readPackScriptSnapshot(repo, 'flow', 'pack')).toEqual({
      workflowId: 'flow', packId: 'pack', baseCommit: sha, baseRef: 'origin/wf/flow',
      files: [{ path: 'scripts/a.py', content: 'print(1)\r\n', mode: '100644' }, { path: 'scripts/sub/empty.sh', content: '', mode: '100755' }],
    });
  });
  it.each(['README.md', 'workflow.pack.yaml', 'packs'])('reads a root Pack whose id %s is also a root file', async packId => {
    git('mv', 'pack/workflow.pack.yaml', 'workflow.pack.yaml');
    git('mv', 'pack/workflows', 'workflows'); git('mv', 'pack/scripts', 'scripts');
    put('workflow.pack.yaml', `id: ${packId}\nworkflows: [flow]\n`);
    if (packId !== 'workflow.pack.yaml') put(packId, 'ordinary file\n');
    ref('wf', commit());
    const result = await readPackScriptSnapshot(repo, 'flow', packId);
    expect(result.packId).toBe(packId);
    expect(result.files.map(file => file.path)).toEqual(['scripts/a.py', 'scripts/sub/empty.sh']);
  });
  it('reads a root Pack when packs/<packId> is an ordinary file', async () => {
    git('mv', 'pack/workflow.pack.yaml', 'workflow.pack.yaml');
    git('mv', 'pack/workflows', 'workflows'); git('mv', 'pack/scripts', 'scripts');
    put('packs/pack', 'unrelated file\n'); ref('wf', commit());
    expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).files).toHaveLength(2);
  });
  it('rejects root manifest conflicts with either nested layout, even if the duplicate is invalid', async () => {
    put('workflow.pack.yaml', 'not a valid manifest'); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/manifest conflict/i);
    git('mv', 'pack', 'tmp-pack'); mkdirSync(path.join(repo, 'packs')); git('mv', 'tmp-pack', 'packs/pack');
    ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/manifest conflict/i);
  });
  it('enforces manifest and workflow identity for root packs', async () => {
    git('mv', 'pack/workflow.pack.yaml', 'workflow.pack.yaml');
    git('mv', 'pack/workflows', 'workflows'); git('mv', 'pack/scripts', 'scripts');
    for (const manifest of [
      'id: other\nworkflows: [flow]\n',
      'id: pack\nworkflows: [other]\n',
      'id: pack\nworkflows: [flow, other]\n',
      'id: pack\nworkflows:\n  - id: flow\n    file: ../flow.yaml\n',
    ]) {
      put('workflow.pack.yaml', manifest); ref('wf', commit());
      await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/identity|workflow|shared|path/i);
    }
    put('workflow.pack.yaml', 'id: pack\nworkflows: [flow]\n');
    put('workflows/flow.yaml', 'id: other\n'); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/workflow identity/i);
    put('workflows/flow.yaml', 'id: flow\n'); ref('wf', commit());
    expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).files).toHaveLength(2);
  });
  it('rejects unsafe root pack entries and allows a pack without scripts', async () => {
    git('mv', 'pack/workflow.pack.yaml', 'workflow.pack.yaml');
    git('mv', 'pack/workflows', 'workflows'); git('mv', 'pack/scripts', 'scripts');
    rmSync(path.join(repo, 'workflow.pack.yaml')); symlinkSync('/etc/passwd', path.join(repo, 'workflow.pack.yaml'));
    ref('wf', commit()); await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/manifest mode/i);
    rmSync(path.join(repo, 'workflow.pack.yaml')); put('workflow.pack.yaml', 'id: pack\nworkflows: [flow]\n');
    rmSync(path.join(repo, 'workflows/flow.yaml')); symlinkSync('/etc/passwd', path.join(repo, 'workflows/flow.yaml'));
    ref('wf', commit()); await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/workflow.*mode/i);
    rmSync(path.join(repo, 'workflows/flow.yaml')); put('workflows/flow.yaml', 'id: flow\n');
    rmSync(path.join(repo, 'scripts'), { recursive: true }); ref('wf', commit());
    expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).files).toEqual([]);
  });
  it('rejects symlinks, unsupported files, invalid UTF-8 and binary content', async () => { symlinkSync('/etc/passwd', path.join(repo, 'pack/scripts/link.py')); ref('wf', commit()); await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/(?:symlink|file|mode)/i); rmSync(path.join(repo, 'pack/scripts/link.py')); put('pack/scripts/invalid.py', Buffer.from([0xff])); ref('wf', commit()); await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/utf|binary/i); put('pack/scripts/invalid.py', Buffer.from([0, 1])); ref('wf', commit()); await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/utf|binary/i); rmSync(path.join(repo, 'pack/scripts/invalid.py')); put('pack/scripts/no.txt', 'hi'); ref('wf', commit()); await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/extension|unsupported/i); });
  it('rejects gitlinks and oversized scripts', async () => {
    const sha = git('rev-parse', 'HEAD');
    git('update-index', '--add', '--cacheinfo', `160000,${sha},pack/scripts/submodule`);
    git('commit', '-qm', 'gitlink'); ref('wf', git('rev-parse', 'HEAD'));
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/file|mode/i);
    git('update-index', '--force-remove', 'pack/scripts/submodule');
    put('pack/scripts/large.py', Buffer.alloc(1024 * 1024 + 1, 65)); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/size limit/i);
  });
  it('enforces the aggregate byte limit', async () => {
    for (let i = 0; i < 9; i++) put(`pack/scripts/large${i}.py`, Buffer.alloc(1024 * 1024, 65));
    ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/size limit/i);
  });
  it('rejects a manifest that does not bind the requested workflow to this pack', async () => {
    for (const manifest of [
      'id: other\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n',
      'id: pack\nworkflows:\n  - id: other\n    file: workflows/flow.yaml\n',
      'id: pack\nworkflows:\n  - id: flow\n    file: ../other.yaml\n',
    ]) {
      put('pack/workflow.pack.yaml', manifest); ref('wf', commit());
      await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/(?:manifest|workflow|path|pack)/i);
    }
  });
  it('rejects a manifest whose workflow file has the wrong identity or is a symlink', async () => {
    put('pack/workflows/flow.yaml', 'id: another-flow\nnodes: []\n'); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/workflow/i);
    rmSync(path.join(repo, 'pack/workflows/flow.yaml'));
    symlinkSync('/etc/passwd', path.join(repo, 'pack/workflows/flow.yaml')); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/workflow/i);
  });
  it('fails closed for shared packs until every workflow has been authorized', async () => {
    put('pack/workflow.pack.yaml', 'id: pack\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n  - id: other\n    file: workflows/other.yaml\n'); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/shared pack/i);
  });
  it('supports a legacy manifest with workflow ids as strings', async () => {
    put('pack/workflow.pack.yaml', 'id: pack\nworkflows: [flow]\n'); ref('wf', commit());
    expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).files).toHaveLength(2);
  });
  it('rejects excessive file counts even when contents are empty', async () => {
    for (let i = 0; i < 501; i++) put(`pack/scripts/empty-${i}.py`, '');
    ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/file.*limit/i);
  });
  it('preserves a UTF-8 BOM', async () => { put('pack/scripts/a.py', Buffer.from([0xef, 0xbb, 0xbf, 0x61])); ref('wf', commit()); expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).files[0].content).toBe('\ufeffa'); });
  it('rejects unsafe identifiers', async () => { for (const id of ['', '../x', '-x', 'a/b', 'a..b', 'x@{y', '.hidden', 'x.lock']) { await expect(readPackScriptSnapshot(repo, id, 'pack')).rejects.toThrow(/invalid/i); await expect(readPackScriptSnapshot(repo, 'flow', id)).rejects.toThrow(/invalid/i); } });
});
