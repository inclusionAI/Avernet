// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { execFileSync } from 'node:child_process';
import { rm } from 'node:fs/promises';
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { readPackScriptSnapshot as readSnapshot } from '../pack-script-snapshot.js';
let repo: string;
function git(...args: string[]): string { return execFileSync('git', args, { cwd: repo, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim(); }
function put(file: string, content: string | Buffer) { const target = path.join(repo, file); mkdirSync(path.dirname(target), { recursive: true }); writeFileSync(target, content); }
function commit(): string { git('add', '-A'); git('commit', '-qm', 'fixture'); return git('rev-parse', 'HEAD'); }
// Inventory fixtures publish the same commit as their wf baseline. Selection
// with an independently advanced draft is covered by pack-version-wf-tag.test.ts.
function ref(name: string, sha: string) {
  git('update-ref', `refs/remotes/origin/${name}/flow`, sha);
  if (name === 'wf') git('update-ref', 'refs/pack-version/active', sha);
}
const readPackScriptSnapshot: typeof readSnapshot = (dir, id, pack, version, options) => readSnapshot(dir, id, pack, version,
  version === 'current' ? { ...options, activeTag: `deploy/${id}/#1` } : options);
beforeEach(() => { repo = mkdtempSync(path.join(tmpdir(), 'pack-snapshot-')); git('init', '-q'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid'); put('pack/workflow.pack.yaml', 'id: pack\nversion: 1\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n'); put('pack/workflows/flow.yaml', 'id: flow\nnodes: []\n'); put('pack/scripts/a.py', 'print(1)\r\n'); put('pack/scripts/sub/empty.sh', ''); commit(); });
afterEach(async () => { vi.restoreAllMocks(); await rm(repo, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });
describe('readPackScriptSnapshot', () => {
  it('reads identical refs preserving bytes, empty files and executable mode', async () => {
    git('update-index', '--chmod=+x', 'pack/scripts/sub/empty.sh'); git('commit', '-qm', 'executable'); const sha = git('rev-parse', 'HEAD'); ref('wf', sha); ref('saved', sha);
    expect(await readPackScriptSnapshot(repo, 'flow', 'pack')).toEqual({ workflowId: 'flow', packId: 'pack', baseCommit: sha, baseRef: 'origin/wf/flow', skippedFiles: { count: 0, paths: [], truncated: false }, files: [{ path: 'scripts/a.py', content: 'print(1)\r\n', mode: '100644' }, { path: 'scripts/sub/empty.sh', content: '', mode: '100755' }] });
  });
  it('returns explicit zero skipped metadata with and without a scripts directory', async () => {
    ref('wf', git('rev-parse', 'HEAD'));
    expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).skippedFiles).toEqual({ count: 0, paths: [], truncated: false });
    rmSync(path.join(repo, 'pack/scripts'), { recursive: true }); ref('wf', commit());
    expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).skippedFiles).toEqual({ count: 0, paths: [], truncated: false });
  });
  it('requires wf and never adopts a saved-only branch', async () => { await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/not.found/i); const sha = git('rev-parse', 'HEAD'); ref('saved', sha); await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/not.found/i); ref('wf', sha); expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).baseRef).toBe('origin/wf/flow'); });
  it('ignores newer, older and divergent saved refs', async () => { const first = git('rev-parse', 'HEAD'); put('pack/scripts/a.py', 'new'); const second = commit(); ref('wf', first); ref('saved', second); expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).baseCommit).toBe(first); ref('wf', second); ref('saved', first); expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).baseRef).toBe('origin/wf/flow'); git('checkout', '-q', '--detach', first); put('pack/scripts/a.py', 'other'); const third = commit(); ref('saved', third); expect((await readPackScriptSnapshot(repo, 'flow', 'pack')).baseCommit).toBe(second); });
  it('reads the requested ref rather than the newer ancestor or descendant', async () => {
    const current = git('rev-parse', 'HEAD'); ref('wf', current);
    put('pack/scripts/a.py', 'draft'); const draft = commit(); ref('wf', draft); git('update-ref', 'refs/pack-version/active', current);
    expect((await readPackScriptSnapshot(repo, 'flow', 'pack', 'current')).baseCommit).toBe(current);
    expect((await readPackScriptSnapshot(repo, 'flow', 'pack', 'draft')).baseCommit).toBe(draft);
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack', 'origin/wf/flow' as never)).rejects.toThrow('Invalid snapshot version');
  });
  it('discovers the root Pack for an explicit version independently of the DB Pack id', async () => {
    git('mv', 'pack/workflow.pack.yaml', 'workflow.pack.yaml'); git('mv', 'pack/workflows', 'workflows'); git('mv', 'pack/scripts', 'scripts');
    ref('wf', commit());
    expect((await readPackScriptSnapshot(repo, 'flow', 'another-pack', 'current')).packId).toBe('pack');
  });
  it('discovers a direct Pack named packs while also checking nested candidates', async () => {
    git('mv', 'pack', 'packs'); put('packs/workflow.pack.yaml', 'id: packs\nworkflows: [flow]\n'); ref('wf', commit());
    expect((await readPackScriptSnapshot(repo, 'flow', 'old', 'current')).packId).toBe('packs');
  });
  it('bounds cumulative YAML read while discovering unrelated manifests', async () => {
    const filler = '#'.repeat(1024 * 1024 - 128);
    for (let i = 0; i < 9; i++) put(`other-${i}/workflow.pack.yaml`, `id: other-${i}\nworkflows: [other]\n#${filler}\n`);
    ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack', 'current')).rejects.toThrow(/discovery limit/i);
  }, 15000);
  it('rejects a matching manifest with a mismatched directory id', async () => {
    put('pack/workflow.pack.yaml', 'id: other\nworkflows: [flow]\n'); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack', 'current')).rejects.toThrow(/identity/i);
  });
  it('omits unsafe skipped names while reporting the exact total', async () => {
    put('pack/scripts/unsafe\u202etxt.so', Buffer.from([0, 1])); put('pack/scripts/okay.sql', 'no content shown'); ref('wf', commit());
    const result = await readPackScriptSnapshot(repo, 'flow', 'pack', 'current');
    expect(result.skippedFiles).toEqual({ count: 2, paths: ['scripts/okay.sql'], truncated: true });
  });
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
      skippedFiles: { count: 0, paths: [], truncated: false },
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
  it('treats packs/<packId>/workflow.pack.yaml as an ordinary root Pack resource', async () => {
    git('mv', 'pack/workflow.pack.yaml', 'workflow.pack.yaml');
    git('mv', 'pack/workflows', 'workflows'); git('mv', 'pack/scripts', 'scripts');
    put('packs/pack/workflow.pack.yaml', 'id: pack\nworkflows: [flow]\n');
    put('packs/pack/workflows/flow.yaml', 'id: flow\nversion: 35\n');
    ref('wf', commit());
    const snapshot = await readPackScriptSnapshot(repo, 'flow', 'pack', 'current', { includeVersionFiles: true });
    expect(snapshot.packPath).toBe('');
    expect(snapshot.versionFiles?.map(file => file.path)).toContain('packs/pack/workflow.pack.yaml');
    expect(snapshot.versionFiles?.map(file => file.path)).toContain('workflows/flow.yaml');
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
  it('rejects symlinks, invalid UTF-8 and binary script content', async () => { symlinkSync('/etc/passwd', path.join(repo, 'pack/scripts/link.py')); ref('wf', commit()); await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/(?:symlink|file|mode)/i); rmSync(path.join(repo, 'pack/scripts/link.py')); put('pack/scripts/invalid.py', Buffer.from([0xff])); ref('wf', commit()); await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/utf|binary/i); put('pack/scripts/invalid.py', Buffer.from([0, 1])); ref('wf', commit()); await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/utf|binary/i); });
  it('ignores ordinary non-script blobs but still rejects unsupported symlinks', async () => {
    put('pack/scripts/native.so', Buffer.from([0, 255])); put('pack/scripts/a.py.bak', 'private backup'); ref('wf', commit());
    expect((await readPackScriptSnapshot(repo, 'flow', 'pack', 'current')).files.map(file => file.path)).toEqual(['scripts/a.py', 'scripts/sub/empty.sh']);
    symlinkSync('/etc/passwd', path.join(repo, 'pack/scripts/link.txt')); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack', 'current')).rejects.toThrow(/file|mode/i);
  });
  it('reports bounded skipped paths without exposing ignored content', async () => {
    for (let i = 0; i < 105; i++) put(`pack/scripts/ignored-${String(i).padStart(3, '0')}.so`, Buffer.from([0, 255]));
    ref('wf', commit());
    const result = await readPackScriptSnapshot(repo, 'flow', 'pack', 'current');
    expect(result.skippedFiles?.count).toBe(105); expect(result.skippedFiles?.paths).toHaveLength(100);
    expect(result.skippedFiles?.truncated).toBe(true);
    expect(JSON.stringify(result)).not.toContain('255');
  });
  it('fails closed for ignored extensions when version is omitted', async () => {
    put('pack/scripts/other.so', Buffer.from([0, 255])); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow('Unsupported script extension');
    const snapshot = await readPackScriptSnapshot(repo, 'flow', 'pack', 'current');
    expect(snapshot.skippedFiles).toEqual({ count: 1, paths: ['scripts/other.so'], truncated: false });
  });
  it.each(['root', 'nested'] as const)('batches discovery across 620 unrelated %s directories', async layout => {
    for (let i = 0; i < 620; i++) put(`${layout === 'root' ? '' : 'packs/'}unrelated-${i}/marker`, 'x');
    put(`${layout === 'root' ? '' : 'packs/'}odd*[x]/workflow.pack.yaml`, 'id: unrelated\nworkflows: [other]\n');
    put(`${layout === 'root' ? '' : 'packs/'}unrelated-0/deep/workflow.pack.yaml`, 'id: deep\nworkflows: [flow]\n');
    const sha = commit(); ref('wf', sha);
    const execute = vi.spyOn(gitExecution, 'executeGit');
    const result = await readPackScriptSnapshot(repo, 'flow', 'pack', 'current');
    expect(result.baseCommit).toBe(sha); expect(result.packId).toBe('pack');
    const calls = execute.mock.calls.filter(([args]) => args.includes('ls-tree'));
    expect(calls.length).toBeLessThan(30);
    for (const [args] of calls.filter(([args]) => args.some(arg => arg.startsWith(':(literal)')))) {
      expect(args).not.toContain('-r');
      expect(args.filter(arg => arg.startsWith(':(literal)')).length).toBeLessThanOrEqual(128);
    }
  }, 30000);
  it('does not follow symlink Pack candidates or recurse into manifest directories', async () => {
    symlinkSync('pack', path.join(repo, 'alias')); ref('wf', commit());
    expect((await readPackScriptSnapshot(repo, 'flow', 'pack', 'current')).packId).toBe('pack');
    put('unrelated/workflow.pack.yaml/deeper/secret', 'never a manifest'); ref('wf', commit());
    const execute = vi.spyOn(gitExecution, 'executeGit');
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack', 'current')).rejects.toThrow(/manifest mode/i);
    const probes = execute.mock.calls.filter(([args]) => args.some(arg => arg.startsWith(':(literal)')));
    expect(probes.length).toBeGreaterThan(0);
    expect(probes.every(([args]) => !args.includes('-r'))).toBe(true);
  });
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
  it.each([
    'id: pack\nworkflows:\n  - id: other\n    file: workflows/other.yaml\n  - id: flow\n    file: workflows/flow.yaml\n',
    'id: pack\nworkflows: [other, flow]\n',
  ])('reads all committed scripts from a multi-workflow Pack', async manifest => {
    put('pack/workflow.pack.yaml', manifest);
    put('pack/scripts/shared.js', 'shared script\n');
    put('pack/scripts/legacy.sh', 'legacy script\n');
    const sha = commit(); ref('wf', sha);
    put('pack/workflow.pack.yaml', 'id: pack\nworkflows: [other]\n');
    put('pack/scripts/shared.js', 'uncommitted change\n');
    expect(await readPackScriptSnapshot(repo, 'flow', 'pack')).toEqual({
      workflowId: 'flow', packId: 'pack', baseCommit: sha, baseRef: 'origin/wf/flow',
      skippedFiles: { count: 0, paths: [], truncated: false },
      files: [
        { path: 'scripts/a.py', content: 'print(1)\r\n', mode: '100644' },
        { path: 'scripts/legacy.sh', content: 'legacy script\n', mode: '100644' },
        { path: 'scripts/shared.js', content: 'shared script\n', mode: '100644' },
        { path: 'scripts/sub/empty.sh', content: '', mode: '100644' },
      ],
    });
  });
  it.each([
    'workflows: [other, another]',
    'workflows: [flow, flow, other]',
    'workflows:\n  - flow\n  - id: flow\n    file: workflows/flow.yaml\n  - other',
    'workflows:\n  - id: flow\n    file: workflows/flow.yaml\n  - id: flow\n    file: workflows/flow.yaml',
  ])('rejects a multi-workflow manifest without exactly one matching workflow: %s', async workflows => {
    put('pack/workflow.pack.yaml', `id: pack\n${workflows}\n`); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/manifest workflow mismatch/i);
  });
  it('rejects a multi-workflow Pack when the matched workflow file has a different id', async () => {
    put('pack/workflow.pack.yaml', 'id: pack\nworkflows: [other, flow]\n');
    put('pack/workflows/flow.yaml', 'id: other\n'); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack')).rejects.toThrow(/workflow identity mismatch/i);
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

describe('full version inventory', () => {
  it('returns every Pack file, including binary and executable bytes, from the selected commit', async () => {
    put('pack/assets/data.bin', Buffer.from([0, 255, 42]));
    put('pack/scripts/binary.py', Buffer.from([0, 255]));
    git('add', '-A'); git('update-index', '--chmod=+x', 'pack/scripts/a.py'); git('commit', '-qm', 'inventory');
    const sha = git('rev-parse', 'HEAD'); ref('wf', sha);
    const snapshot = await readPackScriptSnapshot(repo, 'flow', 'pack', 'current', { includeVersionFiles: true });
    expect(snapshot.packPath).toBe('pack'); expect(snapshot.baseCommit).toBe(sha); expect(snapshot.draftHead).toBe(sha);
    expect(snapshot.versionFiles).toContainEqual({ path: 'assets/data.bin', contentBase64: Buffer.from([0, 255, 42]).toString('base64'), mode: '100644' });
    expect(snapshot.versionFiles).toContainEqual({ path: 'scripts/a.py', contentBase64: Buffer.from('print(1)\r\n').toString('base64'), mode: '100755' });
    expect(snapshot.versionFiles?.map(file => file.path)).toEqual(expect.arrayContaining(['workflow.pack.yaml', 'workflows/flow.yaml', 'scripts/binary.py']));
  });
  it('uses the nested Pack root and reports the observed draft head', async () => {
    git('mv', 'pack', 'tmp-pack'); mkdirSync(path.join(repo, 'packs')); git('mv', 'tmp-pack', 'packs/pack');
    const current = commit(); ref('wf', current); put('packs/pack/readme.md', 'draft'); const draft = commit(); ref('saved', draft);
    const result = await readPackScriptSnapshot(repo, 'flow', 'old', 'current', { includeVersionFiles: true });
    expect(result.packPath).toBe('packs/pack'); expect(result.draftHead).toBe(current); expect(result.baseCommit).toBe(current);
    expect(result.versionFiles?.some(file => file.path === 'readme.md')).toBe(false);
  });
  it('supports a repository-root Pack', async () => {
    git('mv', 'pack/workflow.pack.yaml', 'workflow.pack.yaml'); git('mv', 'pack/workflows', 'workflows'); git('mv', 'pack/scripts', 'scripts');
    ref('wf', commit()); const result = await readPackScriptSnapshot(repo, 'flow', 'pack', 'current', { includeVersionFiles: true });
    expect(result.packPath).toBe(''); expect(result.versionFiles?.map(file => file.path)).toContain('workflow.pack.yaml');
  });
  it('refuses a partial inventory when a symlink is present', async () => {
    symlinkSync('/etc/passwd', path.join(repo, 'pack/link')); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack', 'current', { includeVersionFiles: true })).rejects.toThrow('Unsupported version file');
  });
});

describe('full inventory isolation', () => {
  it('returns a root Pack with packs/ as an ordinary directory', async () => {
    git('mv', 'pack/workflow.pack.yaml', 'workflow.pack.yaml'); git('mv', 'pack/workflows', 'workflows'); git('mv', 'pack/scripts', 'scripts');
    put('packs/private/workflow.pack.yaml', 'id: private\nworkflows: [other]\n'); put('packs/private/workflows/other.yaml', 'id: other\nnodes: []\n');
    put('packs/private/secret.txt', 'sensitive'); ref('wf', commit());
    const snapshot = await readPackScriptSnapshot(repo, 'flow', 'pack', 'current', { includeVersionFiles: true });
    expect(snapshot.packPath).toBe('');
    expect(snapshot.versionFiles?.map(file => file.path)).toEqual(expect.arrayContaining([
      'workflow.pack.yaml',
      'workflows/flow.yaml',
      'packs/private/workflow.pack.yaml',
      'packs/private/workflows/other.yaml',
      'packs/private/secret.txt',
    ]));
  });
  it('rejects paths that the writer cannot safely round trip', async () => {
    put('pack/C:notes.txt', 'text'); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack', 'current', { includeVersionFiles: true })).rejects.toThrow('Unsupported version file');
  });
});


describe('nested Pack boundary', () => {
  it('refuses a direct packs Pack overlapping packs/private', async () => {
    git('mv', 'pack', 'packs'); put('packs/workflow.pack.yaml', 'id: packs\nworkflows: [flow]\n');
    put('packs/private/workflow.pack.yaml', 'id: private\nworkflows: [other]\n'); put('packs/private/secret.txt', 'private'); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'old', 'current', { includeVersionFiles: true })).rejects.toThrow('Mixed Pack layout');
  });
  it('refuses a deeply nested Pack even outside normal manifest discovery', async () => {
    put('pack/deep/nested/workflow.pack.yaml', 'id: private\nworkflows: [other]\n'); put('pack/deep/nested/secret.txt', 'private'); ref('wf', commit());
    await expect(readPackScriptSnapshot(repo, 'flow', 'pack', 'current', { includeVersionFiles: true })).rejects.toThrow('Mixed Pack layout');
  });
});


it('rejects nested Pack scripts in the scripts-only version view', async () => {
  put('pack/scripts/private/workflow.pack.yaml', 'id: private\nworkflows: [other]\n');
  put('pack/scripts/private/scripts/secret.py', 'private content'); ref('wf', commit());
  await expect(readPackScriptSnapshot(repo, 'flow', 'pack', 'current')).rejects.toThrow('Mixed Pack layout');
});
