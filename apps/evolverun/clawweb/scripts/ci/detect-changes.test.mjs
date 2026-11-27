import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const script = fileURLToPath(new URL('./detect-changes.sh', import.meta.url));
const scopeFile = 'apps/evolverun/clawweb/public/sample.txt';
const workflowFile = '.github/workflows/clawweb-ci.yml';
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')));
env.GIT_CONFIG_NOSYSTEM = '1';
env.GIT_CONFIG_GLOBAL = '/dev/null';
const gitPath = spawnSync('bash', ['-c', 'command -v git'], { env, encoding: 'utf8' }).stdout.trim();

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'clawweb-changes-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  function git(...args) {
    const result = spawnSync('git', args, { cwd: root, env, encoding: 'utf8' });
    assert.ifError(result.error);
    assert.equal(result.status, 0, `git ${args.join(' ')}: ${result.stderr}`);
    return result.stdout.trim();
  }
  function write(path, contents) {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    writeFileSync(join(root, path), contents);
  }
  function commit(message) {
    git('add', '--all');
    git('commit', '--quiet', '-m', message);
    return git('rev-parse', 'HEAD');
  }
  function merge() {
    git('checkout', '--quiet', 'main');
    git('merge', '--quiet', '--no-ff', '--no-edit', 'pr');
    return git('rev-parse', 'HEAD');
  }
  git('init', '--quiet', '--initial-branch=main');
  git('config', 'user.name', 'CI scope fixture');
  git('config', 'user.email', 'ci-scope@example.invalid');
  git('config', 'commit.gpgsign', 'false');
  git('config', 'core.hooksPath', '/dev/null');
  write('docs/guide.md', 'initial docs\n');
  write(scopeFile, 'initial ClawWeb\n');
  write(workflowFile, 'name: fixture\n');
  const base = commit('base');
  git('checkout', '--quiet', '-b', 'pr');
  return { root, git, write, commit, merge, base };
}

function detect(f, overrides = {}) {
  const output = join(f.root, '.git', 'ci-output');
  writeFileSync(output, '');
  const result = spawnSync('bash', [script], {
    cwd: f.root,
    env: {
      ...env,
      GITHUB_EVENT_NAME: 'pull_request',
      GITHUB_SHA: f.git('rev-parse', 'HEAD'),
      GITHUB_OUTPUT: output,
      ...overrides,
    },
    encoding: 'utf8',
  });
  assert.ifError(result.error);
  return { ...result, output: readFileSync(output, 'utf8') };
}

function expectSkip(result, skip) {
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.output, `skip=${skip}\n`);
}

function expectFailure(result) {
  assert.notEqual(result.status, 0, result.stdout + result.stderr);
  assert.equal(result.output, '', 'failure must not publish a successful skip decision');
}

// Fault injection delegates every unaffected operation to the real Git binary.
function gitShim(f, body) {
  const directory = join(f.root, '.git', 'shims');
  mkdirSync(directory);
  writeFileSync(join(directory, 'git'), `#!/usr/bin/env bash\n${body}\nexec ${JSON.stringify(gitPath)} "$@"\n`, { mode: 0o755 });
  return `${directory}:${env.PATH}`;
}

test('docs-only PR keeps the check and skips heavy steps', (t) => {
  const f = fixture(t);
  f.write('docs/guide.md', 'PR docs\n');
  f.commit('docs');
  f.merge();
  expectSkip(detect(f), true);
});

test('target branch advances in ClawWeb while the PR changes only docs', (t) => {
  const f = fixture(t);
  f.write('docs/guide.md', 'PR docs\n');
  f.commit('PR docs');
  f.git('checkout', '--quiet', 'main');
  for (let i = 0; i < 71; i += 1) {
    f.write(`apps/evolverun/clawweb/public/target-${i}.txt`, 'target-only change\n');
  }
  const advancedBase = f.commit('target ClawWeb changes');
  const merge = f.merge();
  assert.equal(f.git('rev-parse', `${merge}^1`), advancedBase);
  assert.equal(f.git('diff', '--name-only', f.base, merge, '--', 'apps/evolverun/clawweb').split('\n').length, 71);
  // The old event-base -> merge detector would incorrectly select all 71 files.
  expectSkip(detect(f, { BASE_SHA: f.base }), true);
});

for (const path of [scopeFile, workflowFile, 'apps/evolverun/clawweb/public/new file\nwith newline.txt']) {
  test(`PR change in scope runs heavy steps: ${JSON.stringify(path)}`, (t) => {
    const f = fixture(t);
    f.write(path, 'PR change\n');
    f.commit('scoped change');
    f.merge();
    expectSkip(detect(f), false);
  });
}

for (const path of [scopeFile, workflowFile]) {
  test(`deleting ${path} remains in scope`, (t) => {
    const f = fixture(t);
    f.git('rm', path);
    f.commit('delete scoped file');
    f.merge();
    expectSkip(detect(f), false);
  });

  test(`renaming ${path} out of scope still runs heavy steps`, (t) => {
    const f = fixture(t);
    f.git('mv', path, 'docs/moved.txt');
    f.commit('rename out of scope');
    f.merge();
    expectSkip(detect(f), false);
  });
}

test('similarly named paths outside the scope do not trigger heavy steps', (t) => {
  const f = fixture(t);
  f.write('apps/evolverun/clawweb-other/sample.txt', 'not ClawWeb\n');
  f.write('.github/workflows/clawweb-ci.yml.backup', 'not the workflow\n');
  f.commit('unrelated paths');
  f.merge();
  expectSkip(detect(f), true);
});

test('manual dispatch runs on a non-merge without attempting Git validation', (t) => {
  const f = fixture(t);
  const PATH = gitShim(f, 'echo "Git must not run for dispatch" >&2; exit 73');
  expectSkip(detect(f, { GITHUB_EVENT_NAME: 'workflow_dispatch', PATH }), false);
  expectSkip(detect(f, { GITHUB_EVENT_NAME: 'workflow_dispatch', GITHUB_SHA: undefined, PATH }), false);
});

for (const sha of [undefined, '', 'not-a-commit', '0'.repeat(40), '--help']) {
  test(`missing or invalid merge SHA fails closed: ${JSON.stringify(sha)}`, (t) => {
    const f = fixture(t);
    expectFailure(detect(f, { GITHUB_SHA: sha }));
  });
}

test('a tree object is not a valid PR commit', (t) => {
  const f = fixture(t);
  expectFailure(detect(f, { GITHUB_SHA: f.git('rev-parse', 'HEAD^{tree}') }));
});

test('a root commit cannot be treated as a PR merge', (t) => {
  const f = fixture(t);
  expectFailure(detect(f));
});

test('a single-parent commit cannot be treated as a PR merge', (t) => {
  const f = fixture(t);
  f.write('docs/guide.md', 'PR docs\n');
  f.commit('docs');
  expectFailure(detect(f));
});

test('a merge with more than two parents fails closed', (t) => {
  const f = fixture(t);
  f.write('docs/guide.md', 'PR docs\n');
  const pr = f.commit('docs');
  f.git('checkout', '--quiet', 'main');
  f.write('docs/target.md', 'target docs\n');
  const target = f.commit('target docs');
  const tree = f.git('rev-parse', `${pr}^{tree}`);
  const merge = f.git('commit-tree', tree, '-p', target, '-p', pr, '-p', f.base, '-m', 'three parents');
  expectFailure(detect(f, { GITHUB_SHA: merge }));
});

for (const parent of [1, 2]) {
  test(`missing parent ${parent} object fails closed`, (t) => {
    const f = fixture(t);
    f.write('docs/guide.md', 'PR docs\n');
    f.commit('docs');
    const merge = f.merge();
    const sha = f.git('rev-parse', `${merge}^${parent}`);
    rmSync(join(f.root, '.git', 'objects', sha.slice(0, 2), sha.slice(2)));
    expectFailure(detect(f));
  });
}

for (const command of ['rev-parse', 'show', 'diff']) {
  test(`Git ${command} failure cannot become a successful skip`, (t) => {
    const f = fixture(t);
    f.write('docs/guide.md', 'PR docs\n');
    f.commit('docs');
    f.merge();
    const PATH = gitShim(f, `if [ "$1" = "${command}" ]; then echo "injected ${command} failure" >&2; exit 73; fi`);
    const result = detect(f, { PATH });
    expectFailure(result);
    assert.equal(result.status, 73);
    assert.match(result.stderr, new RegExp(`injected ${command} failure`));
  });
}

test('empty parent metadata cannot become a successful skip', (t) => {
  const f = fixture(t);
  f.write('docs/guide.md', 'PR docs\n');
  f.commit('docs');
  f.merge();
  const PATH = gitShim(f, 'if [ "$1" = "show" ]; then exit 0; fi');
  expectFailure(detect(f, { PATH }));
});

test('malformed parent metadata cannot become a successful skip', (t) => {
  const f = fixture(t);
  f.write('docs/guide.md', 'PR docs\n');
  f.commit('docs');
  f.merge();
  const PATH = gitShim(f, 'if [ "$1" = "show" ]; then echo "invalid-parent another-invalid-parent"; exit 0; fi');
  expectFailure(detect(f, { PATH }));
});

test('a diff that writes partial results and then fails publishes no decision', (t) => {
  const f = fixture(t);
  f.write(scopeFile, 'PR change\n');
  f.commit('scoped change');
  f.merge();
  const PATH = gitShim(f, 'if [ "$1" = "diff" ]; then echo "partial diff output"; exit 73; fi');
  const result = detect(f, { PATH });
  expectFailure(result);
  assert.equal(result.status, 73);
});

test('unexpected event fails closed', (t) => {
  const f = fixture(t);
  expectFailure(detect(f, { GITHUB_EVENT_NAME: 'push' }));
});
