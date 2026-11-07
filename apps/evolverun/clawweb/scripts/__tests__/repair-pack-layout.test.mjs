import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { parse } from 'yaml';

const tool = fileURLToPath(new URL('../repair-pack-layout.mjs', import.meta.url));
const ROOT = 'flows/current.yaml';
const NESTED_MANIFEST = 'packs/pack/workflow.pack.yaml';
const NESTED = 'packs/pack/workflows/flow.yaml';
const rootManifest = '# Keep this metadata and formatting verbatim.\r\nid: pack\r\nversion: 3\r\nworkflows: [{id: flow, file: flows/current.yaml}]\r\nfacades:\r\n  - id: portal\r\n    workflow: flow\r\nassets: [scripts/run.sh, assets/data.bin]\r\n';
const rootYaml = '# root-only-secret\r\nid: flow\r\nversion: 36\r\nfacade: {name: root}\r\nupdatedAt: 2026-11-06T10:00:00Z\r\nnodes: [{id: run, executor: {args: [root-argument]}}]\r\n';
const nestedYaml = '# nested-only-secret\nid: flow\nversion: 35\nfacade: {name: nested}\nupdatedAt: 2026-11-05T10:00:00Z\nnodes: [{id: run, executor: {args: [nested-argument]}}]\n';
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')));
Object.assign(env, { GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_NO_REPLACE_OBJECTS: '1', GIT_TERMINAL_PROMPT: '0' });

function git(repo, args, input) {
  const result = spawnSync('git', ['-C', repo, ...args], { env, input, maxBuffer: 16 * 1024 * 1024 });
  assert.equal(result.status, 0, `git ${args[0]} failed: ${result.stderr}`);
  return result.stdout;
}
function text(repo, ...args) { return git(repo, args).toString().trim(); }
function put(repo, name, content, mode) {
  const file = path.join(repo, name);
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, content);
  if (mode) chmodSync(file, mode);
}
function commit(f) {
  git(f.repo, ['add', '--all']);
  git(f.repo, ['-c', 'commit.gpgSign=false', 'commit', '--no-verify', '-qm', 'Fixture']);
  f.sha = text(f.repo, 'rev-parse', 'HEAD');
  return f.sha;
}
function fixture(t, format = 'sha1') {
  const dir = mkdtempSync(path.join(tmpdir(), 'repair-pack-test-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const repo = path.join(dir, 'repo');
  mkdirSync(repo);
  git(repo, ['init', '-q', '--initial-branch=fixture', `--object-format=${format}`]);
  git(repo, ['config', 'user.name', 'Pack Repair Test']);
  git(repo, ['config', 'user.email', 'pack-repair@example.invalid']);
  put(repo, 'workflow.pack.yaml', rootManifest);
  put(repo, ROOT, rootYaml, 0o755);
  put(repo, NESTED_MANIFEST, 'id: pack\nworkflows: [flow]\n');
  put(repo, NESTED, nestedYaml);
  put(repo, 'scripts/run.sh', '#!/bin/sh\r\nprintf hello\r\n', 0o755);
  put(repo, 'assets/data.bin', Buffer.from([0, 255, 13, 10, 128, 0, 42]));
  put(repo, 'assets/empty.bin', Buffer.alloc(0));
  const f = { dir, repo, sha: '' };
  commit(f);
  git(repo, ['branch', 'wf/flow', f.sha]);
  git(repo, ['branch', 'saved/flow', f.sha]);
  git(repo, ['tag', 'v35', f.sha]);
  return f;
}
function args(f) { return ['--repo', f.repo, '--commit', f.sha, '--pack-id', 'pack', '--workflow-id', 'flow']; }
function run(f, options = [], extraEnv = {}) {
  return spawnSync(process.execPath, [tool, ...args(f), ...options], {
    env: { ...env, ...extraEnv }, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024,
  });
}
function success(result) {
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.equal(result.stderr, '');
  assert.doesNotMatch(result.stdout, /root-only-secret|nested-only-secret|draft-only-secret|printf hello/);
  return JSON.parse(result.stdout);
}
function reject(result, pattern) {
  assert.notEqual(result.status, 0);
  assert.equal(result.stdout, '', 'Failures must not publish a candidate or content');
  assert.doesNotMatch(result.stderr, /MODULE_NOT_FOUND|node:internal|SyntaxError/);
  assert.match(result.stderr, pattern);
  assert.doesNotMatch(result.stderr, /root-only-secret|nested-only-secret|draft-only-secret/);
}
function entries(f, revision) {
  return new Map(git(f.repo, ['ls-tree', '-rz', '--full-tree', revision]).toString().split('\0').filter(Boolean).map(record => {
    const tab = record.indexOf('\t');
    const [mode, type, oid] = record.slice(0, tab).split(' ');
    return [record.slice(tab + 1), { mode, type, oid }];
  }));
}
function filesystem(dir, skipObjects = false, prefix = '') {
  const result = {};
  for (const item of readdirSync(dir, { withFileTypes: true })) {
    const name = prefix + item.name;
    if (skipObjects && name === '.git/objects') continue;
    const file = path.join(dir, item.name);
    if (item.isDirectory()) Object.assign(result, filesystem(file, skipObjects, name + '/'));
    else result[name] = { mode: lstatSync(file).mode, bytes: readFileSync(file).toString('base64') };
  }
  return result;
}
function preserved(f, report, chosen) {
  assert.equal(report.parent, f.sha);
  const candidate = report.candidateCommit;
  assert.match(candidate, /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/);
  const header = text(f.repo, 'cat-file', 'commit', candidate).split('\n\n')[0].split('\n');
  assert.deepEqual(header.filter(line => line.startsWith('parent ')), [`parent ${f.sha}`]);
  const before = entries(f, f.sha);
  const after = entries(f, candidate);
  assert.equal(after.size, before.size - 2);
  for (const [name, entry] of before) {
    if ([NESTED, NESTED_MANIFEST].includes(name)) assert.equal(after.has(name), false);
    else if (name === ROOT) {
      assert.equal(after.get(name).mode, entry.mode);
      assert.deepEqual(git(f.repo, ['cat-file', 'blob', after.get(name).oid]), Buffer.from(chosen));
    } else assert.deepEqual(after.get(name), entry, `non-allowed change: ${name}`);
  }
  assert.deepEqual(git(f.repo, ['show', `${f.sha}:${ROOT}`]), Buffer.from(f.rootExpected ?? rootYaml));
  assert.deepEqual(git(f.repo, ['show', `${f.sha}:${NESTED}`]), Buffer.from(nestedYaml));
  return after;
}

test('dry-run requires an explicit source, reports paths/oids/counts, and writes no objects', t => {
  const f = fixture(t);
  const before = filesystem(f.repo);
  const report = success(run(f, ['--workflow-source', 'nested']));
  assert.equal(report.mode, 'dry-run');
  assert.equal(report.parent, f.sha);
  assert.equal(report.source.path, NESTED);
  assert.equal(report.source.oid, entries(f, f.sha).get(NESTED).oid);
  assert.deepEqual(report.changes.map(change => change.path).sort(), [ROOT, NESTED, NESTED_MANIFEST].sort());
  assert.equal(report.counts.removedFiles, 2);
  assert.equal(report.counts.preservedFiles, entries(f, f.sha).size - 3);
  assert.equal(report.candidateCommit, undefined);
  assert.deepEqual(filesystem(f.repo), before);
});

test('root candidate keeps facades, binary, empty assets and executable modes exactly', t => {
  const f = fixture(t);
  const report = success(run(f, ['--workflow-source', 'root', '--write-candidate']));
  preserved(f, report, rootYaml);
  assert.equal(report.counts.replacedFiles, 0);
  assert.equal(report.changes.length, 2);
  assert.equal(text(f.repo, 'rev-parse', 'HEAD'), f.sha);
});

test('nested is only used when explicitly selected, without any newest-version heuristic', t => {
  const f = fixture(t);
  const report = success(run(f, ['--workflow-source', 'nested', '--write-candidate']));
  preserved(f, report, nestedYaml);
  assert.equal(report.counts.replacedFiles, 1);
});

test('explicit local DB draft JSON preserves v37, fields and ID without a DB write', t => {
  const f = fixture(t);
  const draft = { id: 'flow', version: 37, title: 'draft-only-secret', nodes: [], facade: { name: 'draft' }, updatedAt: '2026-11-07T10:00:00Z', custom: { enabled: false, inputs: ['001', 42] }, executor: { args: ['draft-argument'] } };
  const file = path.join(f.dir, 'db-draft.json');
  writeFileSync(file, JSON.stringify(draft));
  const bytes = readFileSync(file);
  const before = filesystem(f.repo, true);
  const report = success(run(f, ['--workflow-source-file', file, '--write-candidate']));
  assert.equal(report.source.kind, 'json-file');
  assert.equal(report.source.path, file);
  const data = git(f.repo, ['show', `${report.candidateCommit}:${ROOT}`]);
  assert.deepEqual(parse(data.toString()), draft);
  preserved(f, report, data);
  assert.deepEqual(readFileSync(file), bytes);
  assert.deepEqual(filesystem(f.repo, true), before);
});

test('explicit external YAML is copied byte-for-byte, including CRLF and comments', t => {
  const f = fixture(t);
  const file = path.join(f.dir, 'draft.yaml');
  const content = '# draft-only-secret\r\nid: flow\r\nversion: 37\r\nnodes: []\r\n';
  writeFileSync(file, content);
  preserved(f, success(run(f, ['--workflow-source-file', file, '--write-candidate'])), content);
});

test('both modes ignore dirty worktree, staged index, current HEAD and preserve every ref/reflog', t => {
  const f = fixture(t);
  const base = f.sha;
  put(f.repo, 'HEAD-only.txt', 'not part of the explicit historical parent\n');
  commit(f);
  f.sha = base;
  put(f.repo, ROOT, 'id: flow\nversion: 999\n');
  git(f.repo, ['add', ROOT]);
  put(f.repo, ROOT, 'id: flow\nversion: 1000\n');
  put(f.repo, 'scripts/run.sh', 'dirty script\n');
  put(f.repo, 'untracked.txt', 'keep me');
  const before = filesystem(f.repo, true);
  success(run(f, ['--workflow-source', 'root']));
  assert.deepEqual(filesystem(f.repo, true), before);
  preserved(f, success(run(f, ['--workflow-source', 'nested', '--write-candidate'])), nestedYaml);
  assert.deepEqual(filesystem(f.repo, true), before);
});

test('candidate uses no filters, hooks, signing, user index or ambient Git routing', t => {
  const f = fixture(t);
  put(f.repo, '.gitattributes', '*.yaml filter=tripwire text eol=lf\n');
  commit(f);
  f.rootExpected = git(f.repo, ['show', `${f.sha}:${ROOT}`]);
  const marker = path.join(f.dir, 'executed');
  git(f.repo, ['config', 'filter.tripwire.clean', `touch '${marker}'; cat`]);
  git(f.repo, ['config', 'filter.tripwire.required', 'true']);
  git(f.repo, ['config', 'commit.gpgSign', 'true']);
  git(f.repo, ['config', 'gpg.program', '/nonexistent-signing-program']);
  put(f.repo, '.git/hooks/pre-commit', `#!/bin/sh\ntouch '${marker}'\nexit 1\n`, 0o755);
  const userIndex = path.join(f.dir, 'user-index');
  writeFileSync(userIndex, 'not even a Git index');
  const before = filesystem(f.repo, true);
  const report = success(run(f, ['--workflow-source', 'nested', '--write-candidate'], {
    GIT_DIR: '/nonexistent-git-dir', GIT_WORK_TREE: '/nonexistent-worktree', GIT_INDEX_FILE: userIndex,
    GIT_CONFIG_COUNT: '1', GIT_CONFIG_KEY_0: 'user.name', GIT_CONFIG_VALUE_0: 'Untrusted override',
  }));
  preserved(f, report, nestedYaml);
  assert.equal(readFileSync(userIndex, 'utf8'), 'not even a Git index');
  assert.equal(existsSync(marker), false);
  assert.deepEqual(filesystem(f.repo, true), before);
});

test('works against an existing bare repo without creating an index or changing refs', t => {
  const f = fixture(t);
  const bare = path.join(f.dir, 'bare.git');
  git(f.repo, ['clone', '--bare', '--quiet', f.repo, bare]);
  f.repo = bare;
  git(bare, ['config', 'user.name', 'Bare Pack Test']);
  git(bare, ['config', 'user.email', 'bare-pack@example.invalid']);
  const refs = text(bare, 'show-ref');
  success(run(f, ['--workflow-source', 'root']));
  preserved(f, success(run(f, ['--workflow-source', 'nested', '--write-candidate'])), nestedYaml);
  assert.equal(text(bare, 'show-ref'), refs);
  assert.equal(existsSync(path.join(bare, 'index')), false);
});

test('SHA-256 repositories use their full object IDs', t => {
  const f = fixture(t, 'sha256');
  const report = success(run(f, ['--workflow-source', 'nested', '--write-candidate']));
  assert.equal(report.candidateCommit.length, 64);
  preserved(f, report, nestedYaml);
});

test('source selection is mandatory and mutually exclusive; duplicate/unknown flags fail', t => {
  const f = fixture(t);
  for (const options of [[], ['--workflow-source', 'newest'], ['--workflow-source', 'root', '--workflow-source', 'nested'],
    ['--workflow-source', 'root', '--workflow-source-file', '/any.json'], ['--workflow-source', 'root', '--push']]) {
    reject(run(f, options), /source|duplicate|unknown/i);
  }
});

test('input commit must be a full existing commit SHA, not a ref, tag, tree or revision expression', t => {
  const f = fixture(t);
  git(f.repo, ['tag', '-a', '-m', 'Tag', 'annotated', f.sha]);
  for (const sha of ['HEAD', f.sha.slice(0, 12), `${f.sha}^`, text(f.repo, 'rev-parse', `${f.sha}^{tree}`),
    text(f.repo, 'rev-parse', 'annotated'), '0'.repeat(40)]) {
    reject(run({ ...f, sha }, ['--workflow-source', 'root']), /commit|SHA/i);
  }
});

test('rejects symlink and gitlink entries anywhere, even outside the duplicate Pack', async t => {
  for (const kind of ['symlink', 'gitlink']) await t.test(kind, t => {
    const f = fixture(t);
    if (kind === 'symlink') { symlinkSync('scripts/run.sh', path.join(f.repo, 'shortcut')); commit(f); }
    else {
      git(f.repo, ['update-index', '--add', '--cacheinfo', '160000', f.sha, 'module']);
      git(f.repo, ['commit', '--no-verify', '-qm', 'Gitlink fixture']);
      f.sha = text(f.repo, 'rev-parse', 'HEAD');
    }
    reject(run(f, ['--workflow-source', 'root', '--write-candidate']), /mode|symlink|gitlink|unsafe/i);
  });
});

test('rejects extra nested resources instead of silently discarding scripts or binary files', async t => {
  for (const file of ['packs/pack/scripts/run.sh', 'packs/pack/asset.bin', 'packs/pack/workflows/extra.yaml']) await t.test(file, t => {
    const f = fixture(t);
    put(f.repo, file, Buffer.from([0, 255, 10])); commit(f);
    const before = filesystem(f.repo);
    reject(run(f, ['--workflow-source', 'root', '--write-candidate']), /extra|nested/i);
    assert.deepEqual(filesystem(f.repo), before, 'reject before writing any candidate objects');
  });
});

test('rejects multi-workflow manifests and any other manifest, including case aliases', async t => {
  const cases = [
    ['workflow.pack.yaml', 'id: pack\nworkflows: [flow, other]\n'],
    [NESTED_MANIFEST, 'id: pack\nworkflows: [flow, other]\n'],
    ['other/workflow.pack.yaml', 'id: other\nworkflows: [other]\n'],
    ['deep/other/workflow.pack.yaml', 'id: other\nworkflows: [other]\n'],
    ['WORKFLOW.PACK.YAML', 'id: other\nworkflows: [other]\n'],
  ];
  for (const [file, content] of cases) await t.test(file, t => {
    const f = fixture(t);
    put(f.repo, file, content); commit(f);
    reject(run(f, ['--workflow-source', 'root']), /manifest|workflow|alias/i);
  });
});

test('checks pack/workflow IDs in both manifests, both YAMLs and explicit DB JSON', async t => {
  const cases = [
    ['workflow.pack.yaml', 'id: wrong\nworkflows: [{id: flow, file: flows/current.yaml}]\n'],
    [NESTED_MANIFEST, 'id: wrong\nworkflows: [flow]\n'],
    [NESTED_MANIFEST, 'id: pack\nworkflows: [wrong]\n'],
    [ROOT, 'id: wrong\nnodes: []\n'],
    [NESTED, 'id: wrong\nnodes: []\n'],
  ];
  for (const [file, content] of cases) await t.test(file + content.slice(0, 15), t => {
    const f = fixture(t);
    put(f.repo, file, content); commit(f);
    reject(run(f, ['--workflow-source', 'root']), /identity|ID|workflow/i);
  });
  const f = fixture(t);
  const file = path.join(f.dir, 'wrong.json');
  writeFileSync(file, JSON.stringify({ id: 'wrong', version: 37 }));
  reject(run(f, ['--workflow-source-file', file]), /identity|ID/i);
  reject(run(f, ['--workflow-source', 'root', '--pack-id', '../pack']), /duplicate|ID|identity/i);
});

test('rejects dangerous workflow paths and protected-path collisions', async t => {
  for (const file of ['../outside.yaml', '/absolute.yaml', 'a/../../outside.yaml', 'a//flow.yaml', './flow.yaml',
    '.git/config', 'a/.GiT/config', 'C:/flow.yaml', 'a/C:/flow.yaml', 'a\\flow.yaml', 'a\nflow.yaml',
    'workflow.pack.yaml', NESTED, 'scripts/run.sh']) await t.test(JSON.stringify(file), t => {
    const f = fixture(t);
    put(f.repo, 'workflow.pack.yaml', `id: pack\nworkflows: [{id: flow, file: ${JSON.stringify(file)}}]\n`); commit(f);
    reject(run(f, ['--workflow-source', 'root']), /path|unsafe|manifest/i);
  });
});

test('structured YAML parsing rejects duplicate keys, multiple documents, invalid maps and tags', async t => {
  for (const content of ['id: pack\nid: pack\nworkflows: [flow]\n', 'id: pack\nworkflows: [flow]\n---\nid: other\n',
    '[pack, flow]', 'id: pack\nworkflows: !unknown [flow]\n']) await t.test(content.slice(0, 25), t => {
    const f = fixture(t);
    put(f.repo, NESTED_MANIFEST, content); commit(f);
    reject(run(f, ['--workflow-source', 'root']), /YAML|manifest/i);
  });
});

test('rejects absent files, identical duplicate YAML and unrecognized layout', async t => {
  for (const kind of ['missing', 'identical', 'layout']) await t.test(kind, t => {
    const f = fixture(t);
    if (kind === 'missing') rmSync(path.join(f.repo, ROOT));
    if (kind === 'identical') put(f.repo, NESTED, rootYaml);
    if (kind === 'layout') { put(f.repo, 'pack/workflow.pack.yaml', 'id: pack\nworkflows: [flow]\n'); rmSync(path.join(f.repo, NESTED_MANIFEST)); }
    commit(f);
    reject(run(f, ['--workflow-source', 'nested']), /missing|identical|layout|manifest/i);
  });
});

test('external source must be a regular local YAML/JSON file, not symlink, envelope or malformed text', t => {
  const f = fixture(t);
  const json = path.join(f.dir, 'draft.json');
  for (const content of ['{bad json', '{"spec_json":"{\\"id\\":\\"flow\\"}"}', '[{"id":"flow"}]']) {
    writeFileSync(json, content);
    reject(run(f, ['--workflow-source-file', json]), /JSON|identity|ID|object/i);
  }
  symlinkSync(json, path.join(f.dir, 'link.json'));
  reject(run(f, ['--workflow-source-file', path.join(f.dir, 'link.json')]), /regular|symlink/i);
  reject(run(f, ['--workflow-source-file', f.dir]), /regular|file/i);
  reject(run(f, ['--workflow-source-file', path.join(f.dir, 'absent.json')]), /file|source/i);
});

test('candidate identity comes from Git configuration, never a hardcoded fallback', t => {
  const f = fixture(t);
  git(f.repo, ['config', '--unset', 'user.name']);
  git(f.repo, ['config', '--unset', 'user.email']);
  const before = filesystem(f.repo, true);
  reject(run(f, ['--workflow-source', 'root', '--write-candidate']), /identity|ident|commit-tree/i);
  assert.deepEqual(filesystem(f.repo, true), before);
  const report = success(run(f, ['--workflow-source', 'root', '--write-candidate'], {
    GIT_AUTHOR_NAME: 'Explicit Author', GIT_AUTHOR_EMAIL: 'author@example.invalid',
    GIT_COMMITTER_NAME: 'Explicit Committer', GIT_COMMITTER_EMAIL: 'committer@example.invalid',
  }));
  assert.match(text(f.repo, 'cat-file', 'commit', report.candidateCommit), /author Explicit Author <author@example.invalid>/);
});

// Construct tree-only cases that cannot be represented faithfully by a checkout.
function withTreeEntry(f, directory, mode, type, oid, name) {
  const patch = (tree, parts) => {
    const rows = git(f.repo, ['ls-tree', '-z', tree]).toString('binary').split('\0').filter(Boolean);
    if (!parts.length) rows.push(`${mode} ${type} ${oid}\t${name}`);
    else {
      const index = rows.findIndex(row => row.slice(row.indexOf('\t') + 1) === parts[0]);
      assert.notEqual(index, -1);
      const child = rows[index].slice(0, rows[index].indexOf('\t')).split(' ')[2];
      rows[index] = `040000 tree ${patch(child, parts.slice(1))}\t${parts[0]}`;
    }
    return git(f.repo, ['mktree', '-z'], Buffer.from(rows.join('\0') + '\0', 'binary')).toString().trim();
  };
  const tree = patch(text(f.repo, 'rev-parse', `${f.sha}^{tree}`), directory ? directory.split('/') : []);
  f.sha = git(f.repo, ['commit-tree', tree, '-p', f.sha, '-m', 'Tree-only fixture']).toString().trim();
}

test('rejects extra empty nested trees and never drops unrelated empty tree entries', async t => {
  for (const directory of ['packs/pack', '']) await t.test(directory || 'root', t => {
    const f = fixture(t);
    const empty = git(f.repo, ['mktree'], Buffer.alloc(0)).toString().trim();
    withTreeEntry(f, directory, '040000', 'tree', empty, 'empty');
    const before = filesystem(f.repo, true);
    reject(run(f, ['--workflow-source', 'root', '--write-candidate']), /extra|empty|tree|entry/i);
    assert.deepEqual(filesystem(f.repo, true), before);
  });
});

test('rejects unsafe tree paths and case aliases without checking out the tree', async t => {
  for (const name of ['.GiT', 'bad\nname', 'C:drive', 'bad\\name', 'WORKFLOW.PACK.YAML']) await t.test(JSON.stringify(name), t => {
    const f = fixture(t);
    const blob = entries(f, f.sha).get('workflow.pack.yaml').oid;
    withTreeEntry(f, '', '100644', 'blob', blob, name);
    reject(run(f, ['--workflow-source', 'root', '--write-candidate']), /unsafe|path|alias|manifest/i);
  });
});

test('replace refs cannot substitute the explicitly supplied historical commit', t => {
  const f = fixture(t);
  const base = f.sha;
  put(f.repo, ROOT, 'id: flow\nversion: 10000\n');
  commit(f);
  git(f.repo, ['replace', base, f.sha]);
  f.sha = base;
  const before = filesystem(f.repo, true);
  preserved(f, success(run(f, ['--workflow-source', 'root', '--write-candidate'])), rootYaml);
  assert.deepEqual(filesystem(f.repo, true), before);
});

test('invalid UTF8, NUL, oversized source files and ambiguous file extensions fail closed', t => {
  const f = fixture(t);
  for (const bytes of [Buffer.from([0xff, 0xfe]), Buffer.from('id: flow\n\0'), Buffer.alloc(1024 * 1024 + 1, 65)]) {
    const file = path.join(f.dir, 'unsafe.yaml');
    writeFileSync(file, bytes);
    reject(run(f, ['--workflow-source-file', file]), /UTF|text|YAML|large|size|NUL/i);
  }
  const file = path.join(f.dir, 'draft.txt');
  writeFileSync(file, '{"id":"flow","version":37}');
  reject(run(f, ['--workflow-source-file', file]), /extension|format|source/i);
});

test('Git subprocess audit contains plumbing only, with an isolated temporary index cleaned up', t => {
  const f = fixture(t);
  const realGit = spawnSync('which', ['git'], { env, encoding: 'utf8' }).stdout.trim();
  const bin = path.join(f.dir, 'bin');
  mkdirSync(bin);
  const log = path.join(f.dir, 'git-commands.log');
  const indexLog = path.join(f.dir, 'git-index.log');
  put(bin, 'git', `#!/bin/sh\nprintf 'CALL\\n' >> '${log}'\nprintf '%s\\n' "$@" >> '${log}'\nprintf '%s\\n' "$GIT_INDEX_FILE" >> '${indexLog}'\nexec '${realGit}' "$@"\n`, 0o755);
  success(run(f, ['--workflow-source', 'nested', '--write-candidate'], { PATH: `${bin}${path.delimiter}${env.PATH}` }));
  const calls = readFileSync(log, 'utf8').split('CALL\n').filter(Boolean).map(call => call.trim().split('\n'));
  const commands = calls.map(argv => {
    let i = 0;
    while (['-C', '-c'].includes(argv[i])) i += 2;
    return argv[i];
  });
  assert.ok(commands.includes('commit-tree'));
  assert.ok(calls.some(argv => argv.includes('--no-filters')));
  for (const forbidden of ['push', 'fetch', 'checkout', 'switch', 'update-ref', 'reset', 'add', 'commit', 'tag', 'branch']) {
    assert.ok(!commands.includes(forbidden), `forbidden Git command: ${forbidden}`);
  }
  const indexes = readFileSync(indexLog, 'utf8').split('\n').filter(Boolean);
  assert.ok(indexes.length > 0, 'candidate construction must use a temporary index');
  for (const index of indexes) {
    assert.notEqual(index, path.join(f.repo, '.git/index'));
    assert.equal(existsSync(path.dirname(index)), false, 'temporary index directory must be removed');
  }
});

test('validates explicit CLI identities rather than only discovering from manifests', t => {
  const f = fixture(t);
  for (const [flag, id] of [['--pack-id', '../pack'], ['--workflow-id', '../flow'], ['--pack-id', 'wrong'], ['--workflow-id', 'wrong']]) {
    const options = args(f);
    options[options.indexOf(flag) + 1] = id;
    const result = spawnSync(process.execPath, [tool, ...options, '--workflow-source', 'root'], { env, encoding: 'utf8' });
    reject(result, /ID|identity|workflow/i);
  }
});

test('JSON conversion must not silently round unsafe integers or decimal literals', t => {
  const f = fixture(t);
  const file = path.join(f.dir, 'precise.json');
  for (const literal of ['9007199254740993', '0.12345678901234567890123456789', '1e400']) {
    writeFileSync(file, `{"id":"flow","version":37,"executor":{"args":[${literal}]}}`);
    const result = run(f, ['--workflow-source-file', file, '--write-candidate']);
    if (result.status === 0) {
      const report = success(result);
      const bytes = git(f.repo, ['show', `${report.candidateCommit}:${ROOT}`]).toString();
      assert.ok(bytes.includes(literal), 'precise JSON number must not be silently rounded or become infinity');
    } else reject(result, /precision|number|JSON/i);
  }
});

test('root and nested workflow YAML paths may differ; root manifest always determines destination', t => {
  const f = fixture(t);
  put(f.repo, NESTED_MANIFEST, 'id: pack\nworkflows: [{id: flow, file: legacy.yaml}]\n');
  git(f.repo, ['mv', NESTED, 'packs/pack/legacy.yaml']);
  commit(f);
  const report = success(run(f, ['--workflow-source', 'nested', '--write-candidate']));
  assert.equal(report.source.path, 'packs/pack/legacy.yaml');
  const after = entries(f, report.candidateCommit);
  assert.equal(after.has('packs/pack/legacy.yaml'), false);
  assert.equal(after.has(NESTED_MANIFEST), false);
  assert.deepEqual(git(f.repo, ['show', `${report.candidateCommit}:${ROOT}`]), Buffer.from(nestedYaml));
  assert.deepEqual(after.get('workflow.pack.yaml'), entries(f, f.sha).get('workflow.pack.yaml'));
});
