#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { classifyResult, writeReports } from './test-reports.mjs';

export function workspacePackages(root) {
  const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
  const names = new Set();
  return manifest.workspaces.flatMap(pattern => {
    // Only the literal directories and trailing /* used by these workspaces.
    if (isAbsolute(pattern) || pattern.split('/').includes('..') || /[?{}!]/.test(pattern) || pattern.slice(0, -2).includes('*')) {
      throw new Error(`Unsupported workspace path: ${pattern}`);
    }
    const paths = pattern.endsWith('/*')
      ? readdirSync(resolve(root, pattern.slice(0, -2)), { withFileTypes: true })
        .filter(entry => entry.isDirectory()).map(entry => join(pattern.slice(0, -2), entry.name)).sort()
      : [pattern];
    return paths.filter(path => existsSync(resolve(root, path, 'package.json'))).map(path => {
      const pkg = JSON.parse(readFileSync(resolve(root, path, 'package.json'), 'utf8'));
      if (!pkg.name || names.has(pkg.name)) throw new Error(`Missing/duplicate package name: ${path}`);
      names.add(pkg.name);
      return { name: pkg.name, path, root: realpathSync(resolve(root, path)), scripts: pkg.scripts || {} };
    });
  });
}

function gitSha(root) {
  const result = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' });
  return result.status === 0 ? result.stdout.trim() : null;
}

export function main(args = process.argv.slice(2)) {
  const mode = args[0] || 'test';
  if (!['test', 'build', 'check'].includes(mode) || args.length > 2) {
    throw new Error('usage: run-tests.mjs [test|build|check] [workspace-root]');
  }
  const root = realpathSync(resolve(args[1] || process.cwd()));
  const packages = workspacePackages(root);
  if (!packages.length) throw new Error('No workspace packages found');
  const npm = process.env.npm_execpath;
  if (mode !== 'test') {
    if (!npm) throw new Error('Run build/check through npm run ci:build or ci:check');
    for (const pkg of packages) {
      if (!pkg.scripts[mode]) throw new Error(`${pkg.name}: missing ${mode} script`);
      const child = spawnSync(process.execPath, [npm, 'run', mode, '--workspace', pkg.name], {
        cwd: root, stdio: 'inherit', timeout: 600_000, killSignal: 'SIGKILL',
      });
      if (child.error || child.signal || child.status !== 0) return 1;
    }
    return 0;
  }

  const output = join(root, 'test-results');
  // 以下为安全注释COSEC：只清理固定报告目录，拒绝指向其他目录的软链接。
  if (existsSync(output) && realpathSync(output) !== output) throw new Error('test-results must not be a symlink');
  rmSync(output, { recursive: true, force: true });
  mkdirSync(output, { recursive: true });
  const rows = [];
  const metadata = {
    node: process.version, npm: process.env.npm_config_user_agent || null,
    workspaceSha: gitSha(root), toolSha: gitSha(dirname(fileURLToPath(import.meta.url))),
    requestedRef: process.env.AVERNET_REF || null,
    startedAt: new Date().toISOString(),
  };
  for (const [index, pkg] of packages.entries()) {
    const id = `${index + 1}-${pkg.name.replace(/[^a-zA-Z0-9._-]/g, '-')}`;
    const dir = join(output, 'packages', id);
    mkdirSync(dir, { recursive: true });
    console.log(`\n[ClawWeb CI] Testing ${pkg.name}`);
    const row = { name: pkg.name, path: pkg.path, root: pkg.root, reportDir: dir };
    const started = Date.now();
    try {
      if (!pkg.scripts.test) throw new Error('Missing test script (NOT_CONFIGURED)');
      if (!/^vitest run(?:\s|$)/.test(pkg.scripts.test)) throw new Error('Expected existing vitest run test script');
      const require = createRequire(join(pkg.root, 'package.json'));
      const vitestManifest = require.resolve('vitest/package.json');
      const version = JSON.parse(readFileSync(vitestManifest, 'utf8')).version;
      row.vitest = version;
      const vitestCli = join(dirname(vitestManifest), 'vitest.mjs');
      const scriptArgs = pkg.scripts.test.slice('vitest run'.length).trim().split(/\s+/).filter(Boolean);
      // Existing scripts only have --config and --passWithNoTests; do not evaluate shell text.
      if (scriptArgs.some(arg => !/^[a-zA-Z0-9._/-]+$/.test(arg))) throw new Error('Unsupported test script arguments');
      const child = spawnSync(process.execPath, [vitestCli, 'run', ...scriptArgs,
        '--maxWorkers=2', '--minWorkers=1', '--reporter=default', '--reporter=json', '--reporter=junit',
        `--outputFile.json=${join(dir, 'results.json')}`, `--outputFile.junit=${join(dir, 'junit.xml')}`,
        '--coverage.enabled', '--coverage.provider=v8', '--coverage.all', '--coverage.reportOnFailure',
        `--coverage.reportsDirectory=${join(dir, 'coverage')}`,
        '--coverage.reporter=json', '--coverage.reporter=text-summary', '--coverage.reporter=html',
        '--coverage.include=server/**/*.{ts,tsx,js,jsx}', '--coverage.include=web/**/*.{ts,tsx,js,jsx}',
        '--coverage.include=src/**/*.{ts,tsx,js,jsx}',
        '--coverage.exclude=**/*.d.ts', '--coverage.exclude=**/*.{test,spec}.*',
        '--coverage.exclude=**/{__tests__,test,tests,fixtures,__fixtures__,dist,dist-server,node_modules}/**',
      ], { cwd: pkg.root, stdio: 'inherit', timeout: 300_000, killSignal: 'SIGKILL',
        env: { ...process.env, NODE_ENV: 'test', CI: 'true' } });
      row.exitCode = child.status;
      row.signal = child.signal;
      if (child.error) row.error = child.error.message;
      const json = JSON.parse(readFileSync(join(dir, 'results.json'), 'utf8'));
      Object.assign(row, classifyResult(json, child));
      if (!existsSync(join(dir, 'junit.xml'))) throw new Error('Missing JUnit report');
      row.coverageFile = join(dir, 'coverage', 'coverage-final.json');
      if (!existsSync(row.coverageFile)) {
        row.coverageFile = null;
        if (row.status !== 'NO_TESTS') throw new Error('Missing coverage report');
      }
    } catch (error) {
      row.status = 'ERROR';
      row.error = error.message;
    }
    row.durationMs = Date.now() - started;
    rows.push(row);
  }
  const result = writeReports(root, output, rows, metadata);
  console.log(`[ClawWeb CI] ${result ? 'FAILED' : 'PASSED'}; reports: ${output}`);
  return result;
}

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.exitCode = main(); }
  catch (error) { console.error(`[ClawWeb CI] ERROR: ${error.message}`); process.exitCode = 1; }
}
