import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { main, selectWorkspacePackages } from './run-tests.mjs';
import { classifyResult, writeReports } from './test-reports.mjs';
import { verifyPackageBoundaries } from './verify-package-boundaries.mjs';

function verifyFixture(root) {
  return verifyPackageBoundaries(['workflow', 'shared'].map(name => ({ root: join(root, 'public', name) })));
}

const json = { numTotalTests: 1, numPassedTests: 1, numFailedTests: 0,
  numPendingTests: 0, numFailedTestSuites: 0, success: true, testResults: [{ status: 'passed' }] };
test('nonzero exit cannot be overridden by a passing JSON report', () => {
  assert.equal(classifyResult(json, { status: 1 }).status, 'FAIL');
});
test('failed JSON cannot be overridden by exit zero', () => {
  assert.equal(classifyResult({ ...json, success: false, numFailedTests: 1 }, { status: 0 }).status, 'FAIL');
});
test('zero tests with suite error is failure, not NO_TESTS', () => {
  assert.equal(classifyResult({ ...json, numTotalTests: 0, numPassedTests: 0,
    numFailedTestSuites: 1 }, { status: 0 }).status, 'FAIL');
});
test('empty successful collection is explicitly NO_TESTS', () => {
  assert.equal(classifyResult({ ...json, numTotalTests: 0, numPassedTests: 0,
    testResults: [] }, { status: 0 }).status, 'NO_TESTS');
});
test('invalid counters are an error', () => {
  assert.throws(() => classifyResult({ ...json, numPassedTests: undefined }, { status: 0 }));
});
test('earlier failure survives later success; escaped XML remains readable', () => {
  const root = mkdtempSync(join(tmpdir(), 'clawweb-report-'));
  try {
    const rows = ['FAIL', 'PASS'].map((status, index) => {
      const reportDir = join(root, String(index)); mkdirSync(reportDir);
      writeFileSync(join(reportDir, 'junit.xml'), '<testsuites><testsuite name="sample"><testcase name="a &amp; b"/></testsuite></testsuites>');
      return { name: `pkg-${index}`, path: `pkg-${index}`, root, reportDir,
        status, total: 1, passed: index, failed: 1-index };
    });
    assert.equal(writeReports(root, root, rows, { node: process.version }), 1);
    assert.equal(JSON.parse(readFileSync(join(root, 'summary.json'))).totals.failed, 1);
    assert.match(readFileSync(join(root, 'junit.xml'), 'utf8'), /a &amp; b/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('merges entity-heavy JUnit logs verbatim without exhausting the parser budget', () => {
  const root = mkdtempSync(join(tmpdir(), 'clawweb-report-logs-'));
  try {
    const reportDir = join(root, 'package'); mkdirSync(reportDir);
    const log = '&quot;'.repeat(1_000_001);
    writeFileSync(join(reportDir, 'junit.xml'), `<testsuites><testsuite name="logs"><testcase name="a &amp; b"/>
      <system-out>${log}</system-out><system-err>&lt;error&gt;</system-err></testsuite></testsuites>`);
    const row = { name: 'workflow', path: 'workflow', root, reportDir, status: 'PASS', total: 1, passed: 1, failed: 0 };
    assert.equal(writeReports(root, root, [row], { node: process.version }), 0);
    const merged = readFileSync(join(root, 'junit.xml'), 'utf8');
    assert.ok(merged.includes(log));
    assert.match(merged, /name="a &amp; b"/);
    assert.match(merged, /<system-err>&lt;error&gt;<\/system-err>/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('still rejects JUnit entity declarations when console logs are opaque', () => {
  const root = mkdtempSync(join(tmpdir(), 'clawweb-report-dtd-'));
  try {
    const reportDir = join(root, 'package'); mkdirSync(reportDir);
    writeFileSync(join(reportDir, 'junit.xml'), '<!DOCTYPE testsuites [<!ENTITY x "unsafe">]><testsuites/>');
    const row = { name: 'workflow', path: 'workflow', root, reportDir, status: 'PASS', total: 1, passed: 1, failed: 0 };
    assert.equal(writeReports(root, root, [row], { node: process.version }), 1);
    assert.equal(row.status, 'ERROR');
    assert.match(row.error, /DTD is not allowed/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('collection errors are tracked separately from assertion failures', () => {
  const result = classifyResult({ ...json, testResults: [{ status: 'failed', assertionResults: [] }] }, { status: 1 });
  assert.equal(result.collectionErrors, 1);
});
test('signals and timeouts cannot produce success', () => {
  assert.equal(classifyResult(json, { status: null, signal: 'SIGKILL' }).status, 'FAIL');
  assert.equal(classifyResult(json, { status: 0, error: new Error('timeout') }).status, 'FAIL');
});
test('keeps passing console output in JUnit and failing output visible without weakening failures', () => {
  const root = mkdtempSync(join(tmpdir(), 'clawweb-console-'));
  try {
    const pkg = join(root, 'public/sample');
    mkdirSync(join(pkg, 'src'), { recursive: true });
    writeFileSync(join(root, 'package.json'), JSON.stringify({ workspaces: ['public/sample'] }));
    writeFileSync(join(pkg, 'package.json'), JSON.stringify({
      name: 'console-fixture', type: 'module', scripts: { test: 'vitest run' },
    }));
    symlinkSync(fileURLToPath(new URL('../../node_modules', import.meta.url)), join(pkg, 'node_modules'), 'dir');
    writeFileSync(join(pkg, 'src/sample.js'), 'export const value = 1;');
    writeFileSync(join(pkg, 'src/passes.test.js'), `
      import { expect, it } from 'vitest';
      import { value } from './sample.js';
      it('passes', () => { console.log('passing-console-marker'); expect(value).toBe(1); });
    `);
    writeFileSync(join(pkg, 'src/fails.test.js'), `
      import { expect, it } from 'vitest';
      import { value } from './sample.js';
      it('fails', () => { console.error('failing-console-marker'); expect(value).toBe(2); });
    `);
    const child = spawnSync(process.execPath, [fileURLToPath(new URL('./run-tests.mjs', import.meta.url)), 'test', root], {
      encoding: 'utf8', timeout: 30_000, env: { ...process.env, CI: 'true' },
    });
    assert.ifError(child.error);
    assert.equal(child.status, 1);
    const output = child.stdout + child.stderr;
    assert.doesNotMatch(output, /passing-console-marker/);
    assert.match(output, /failing-console-marker/);
    const report = JSON.parse(readFileSync(join(root, 'test-results/summary.json'), 'utf8'));
    assert.equal(report.totals.total, 2);
    assert.equal(report.totals.failed, 1);
    const junit = readFileSync(join(root, 'test-results/junit.xml'), 'utf8');
    assert.match(junit, /passing-console-marker/);
    assert.match(junit, /failing-console-marker/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
test('package scope selects only OCB-owned internal packages', () => {
  const packages = [
    { name: '@avernet/example', path: '.build/avernet/apps/evolverun/clawweb/public/modules/example' },
    { name: '@ocb/example', path: 'internal/modules/example' },
    { name: '@ocb/host', path: 'internal/bootstrap/clawweb' },
  ];
  assert.deepEqual(selectWorkspacePackages(packages, 'internal').map(pkg => pkg.name),
    ['@ocb/example', '@ocb/host']);
  assert.deepEqual(selectWorkspacePackages(packages, 'public').map(pkg => pkg.name),
    ['@avernet/example']);
});

function fixture(t, source) {
  const root = mkdtempSync(join(tmpdir(), 'clawweb-boundaries-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const name of ['workflow', 'shared']) {
    mkdirSync(join(root, 'public', name), { recursive: true });
    writeFileSync(join(root, 'public', name, 'package.json'), JSON.stringify({ name: `@avernet/${name}` }));
  }
  const directory = join(root, 'public/workflow/web/__tests__');
  mkdirSync(directory, { recursive: true });
  writeFileSync(join(directory, 'sample.test.tsx'), source);
  return root;
}

for (const source of [
  "import { x } from '../../../shared/client'",
  "export { x } from '../../../shared/client'",
  "const x = import('../../../shared/client')",
  "vi.mock('@avernet/shared/client', () => import('../../../shared/client'))",
  "vi.mock('../../../shared/client', () => ({}))",
  "vi.importActual('../../../shared/client')",
  "require('../../../shared/client')",
]) {
  test(`rejects cross-package references in tests: ${source}`, (t) => {
    assert.throws(() => verifyFixture(fixture(t, source)), /relative import escapes package/);
  });
}

test('accepts public package entry points and package-local relative imports', (t) => {
  const root = fixture(t, "import { x } from '@avernet/clawweb-shared/web/api/client'; import { y } from '../hooks'; vi.mock('../local')");
  assert.equal(verifyFixture(root), 2);
});

test('rejects public imports of internal packages', (t) => {
  assert.throws(() => verifyFixture(fixture(t, "import x from '@ocb/private'")), /imports OCB/);
});

test('resolves symlinked directories even when the imported file has not been generated', (t) => {
  const root = fixture(t, "import x from './linked/not-generated'");
  symlinkSync(join(root, 'public/shared'), join(root, 'public/workflow/web/__tests__/linked'), 'dir');
  assert.throws(() => verifyFixture(root), /relative import escapes package/);
});

test('does not check generated output or installed dependencies', (t) => {
  const root = fixture(t, "import x from '../hooks'");
  for (const name of ['dist', 'dist-server', 'node_modules']) {
    const directory = join(root, 'public/workflow', name);
    mkdirSync(directory);
    writeFileSync(join(directory, 'generated.js'), "import x from '../../shared/client'");
  }
  assert.equal(verifyFixture(root), 2);
});

test('fails closed when no packages are discovered', () => {
  assert.throws(() => verifyPackageBoundaries([]), /No public ClawWeb packages/);
});


test('existing ci:check entry rejects a boundary violation before invoking package checks', (t) => {
  const root = fixture(t, "import x from '../../../shared/client'");
  writeFileSync(join(root, 'package.json'), JSON.stringify({ workspaces: ['public/workflow', 'public/shared'] }));
  assert.throws(() => main(['check', root]), /relative import escapes package/);
});
