import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, relative, sep } from 'node:path';
import { XMLParser, XMLBuilder } from 'fast-xml-parser';
import coverage from 'istanbul-lib-coverage';
import libReport from 'istanbul-lib-report';
import reports from 'istanbul-reports';

export function classifyResult(json, child) {
  const counts = {
    total: json.numTotalTests, passed: json.numPassedTests, failed: json.numFailedTests,
    skipped: json.numPendingTests, todo: json.numTodoTests || 0,
    files: json.testResults?.length, failedSuites: json.numFailedTestSuites || 0,
    collectionErrors: (json.testResults || []).filter(suite => suite.status === 'failed' && !suite.assertionResults?.length).length,
  };
  if (Object.values(counts).some(n => !Number.isInteger(n) || n < 0)) throw new Error('Invalid test counts');
  const failed = child.error || child.signal || child.status !== 0 || !json.success
    || counts.failed || counts.failedSuites || json.testResults.some(suite => suite.status === 'failed');
  return { ...counts, status: failed ? 'FAIL' : counts.total ? 'PASS' : 'NO_TESTS' };
}

export function writeReports(root, output, rows, metadata) {
  const map = coverage.createCoverageMap({});
  const suites = [];
  const parser = new XMLParser({ ignoreAttributes: false,
    processEntities: { maxTotalExpansions: 1_000_000, maxExpandedLength: 50_000_000 } });
  const builder = new XMLBuilder({ ignoreAttributes: false, format: true });
  for (const row of rows) {
    try {
      if (row.coverageFile) {
        const owned = coverage.createCoverageMap({});
        const data = JSON.parse(readFileSync(row.coverageFile, 'utf8'));
        for (const [path, value] of Object.entries(data)) {
          const local = relative(row.root, path);
          if (local === '..' || local.startsWith(`..${sep}`) || isAbsolute(local)) continue;
          const key = join(row.path, local).split(sep).join('/');
          if (row.status === 'NO_TESTS') {
            // No test executed: V8 synthetic function counters are not test coverage.
            for (const name of Object.keys(value.s)) value.s[name] = 0;
            for (const name of Object.keys(value.f)) value.f[name] = 0;
            for (const name of Object.keys(value.b)) value.b[name] = value.b[name].map(() => 0);
          }
          owned.addFileCoverage({ ...value, path: key });
        }
        row.coverage = owned.files().length ? owned.getCoverageSummary().toJSON() : null;
        map.merge(owned);
      }
      const file = join(row.reportDir, 'junit.xml');
      if (existsSync(file)) {
        const xml = readFileSync(file, 'utf8');
        // 以下为安全注释COSEC：报告只接受无 DTD/实体声明的本地 JUnit XML。
        if (/<!DOCTYPE|<!ENTITY/i.test(xml)) throw new Error('DTD is not allowed in JUnit reports');
        const data = parser.parse(xml);
        const list = data.testsuites?.testsuite ?? data.testsuite;
        if (list) for (const suite of Array.isArray(list) ? list : [list]) {
          suite['@_name'] = `${row.name}: ${suite['@_name'] || 'tests'}`;
          suites.push(suite);
        }
      }
    } catch (error) { row.status = 'ERROR'; row.error = error.message; }
    if (row.status === 'ERROR') {
      suites.push({ '@_name': `${row.name}: CI infrastructure`, '@_tests': 1, '@_errors': 1,
        testcase: { '@_name': 'runner/report error (not a business test)', error: { '#text': row.error || 'Report error' } } });
    }
  }
  const failed = rows.some(row => !['PASS', 'NO_TESTS'].includes(row.status));
  const totals = Object.fromEntries(['total', 'passed', 'failed', 'skipped', 'todo', 'files', 'failedSuites']
    .map(key => [key, rows.reduce((sum, row) => sum + (row[key] || 0), 0)]));
  const summary = { ...metadata, finishedAt: new Date().toISOString(), status: failed ? 'FAIL' : 'PASS',
    totals, coverageComplete: rows.every(row => row.coverage != null && row.status !== 'ERROR' && !row.collectionErrors),
    coverage: map.files().length ? map.getCoverageSummary().toJSON() : null,
    packages: rows.map(({ root: ignored, reportDir, coverageFile, ...row }) => row) };
  const coverageDir = join(output, 'coverage');
  mkdirSync(coverageDir, { recursive: true });
  const context = libReport.createContext({ dir: coverageDir, coverageMap: map });
  for (const type of ['cobertura', 'lcovonly', 'json', 'json-summary']) reports.create(type).execute(context);
  summary.groups = Object.fromEntries(['public', 'internal'].map(group => {
    const selected = rows.filter(row => row.path.startsWith('internal/') === (group === 'internal'));
    return [group, { packages: selected.length, passed: selected.reduce((n, row) => n + (row.passed || 0), 0),
      failed: selected.reduce((n, row) => n + (row.failed || 0), 0) }];
  }));
  writeFileSync(join(output, 'junit.xml'), builder.build({ testsuites: { testsuite: suites } }));
  writeFileSync(join(output, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
  const pct = row => row.coverage ? ['lines', 'statements', 'functions', 'branches']
    .map(key => `${row.coverage[key].pct}%`).join(' / ') : 'unavailable';
  const text = [`# ClawWeb tests: ${summary.status}`, '',
    `Node ${metadata.node}; workspace ${metadata.workspaceSha}; tools ${metadata.toolSha}`, '',
    '| Package | Status | Files | Passed | Failed | Skipped | Todo | Lines / statements / functions / branches |',
    '|---|---|---:|---:|---:|---:|---:|---|',
    ...rows.map(row => `| ${row.name} | ${row.status} | ${row.files ?? 'N/A'} | ${row.passed ?? 'N/A'} | ${row.failed ?? 'N/A'} | ${row.skipped ?? 'N/A'} | ${row.todo ?? 'N/A'} | ${pct(row)} |`), '',
    `Totals: ${totals.passed} passed, ${totals.failed} failed, ${totals.skipped} skipped, ${totals.todo} todo.`,
    `Coverage: ${summary.coverageComplete ? 'complete' : 'INCOMPLETE (see unavailable packages or collectionErrors in JSON)'}; ${pct(summary)}.`,
    'Coverage counts each package’s own source tested by its own tests; no cross-package dist duplication.',
    'NO_TESTS is not a passed test. Infrastructure errors are listed separately from business test counts.', '',
    ...rows.filter(row => row.error).map(row => `- ${row.name}: ${row.error.replace(/[\r\n]/g, ' ')}`), ''].join('\n');
  writeFileSync(join(output, 'summary.md'), text);
  console.log(text);
  return failed ? 1 : 0;
}
