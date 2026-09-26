import type {
  AssertionResult,
  NodeExecutionReport,
  TestCaseReport,
  TestReport,
} from "../types.js";

// ── Exit Codes (6.8) ──

export const EXIT_PASS = 0;
export const EXIT_FAIL = 1;
export const EXIT_ERROR = 2;

export function computeExitCode(report: TestReport): number {
  if (report.status === "error") return EXIT_ERROR;
  if (report.status === "failed") return EXIT_FAIL;
  return EXIT_PASS;
}

// ── Text Format (6.2, 6.4) ──

function renderAssertionText(result: AssertionResult, indent: string): string {
  const icon = result.passed ? "✓" : "✗";
  const matcher = result.matcher ? ` ${result.matcher}` : "";
  const path = result.path ? ` ${result.path}` : "";

  if (result.passed) {
    return `${indent}${icon}${matcher}${path}`;
  }

  const lines: string[] = [`${indent}${icon}${matcher}${path}`];

  if (result.expected !== undefined) {
    lines.push(`${indent}  Expected: ${JSON.stringify(result.expected)}`);
  }
  if (result.actual !== undefined) {
    lines.push(`${indent}  Actual:   ${JSON.stringify(result.actual)}`);
  }
  if (result.message) {
    lines.push(`${indent}  ${result.message}`);
  }

  return lines.join("\n");
}

function renderNodeReportText(report: NodeExecutionReport): string {
  const status = report.nodeStatus;
  const icon = status === "succeeded" ? "PASS" : status === "skipped" ? "SKIP" : "FAIL";
  const header = `${icon}  ${report.nodeId} (${status})`;

  if (report.assertions.length === 0) {
    return header;
  }

  const assertionLines = report.assertions
    .map((a) => renderAssertionText(a, "  "))
    .join("\n");

  return `${header}\n${assertionLines}`;
}

function renderTestCaseText(report: TestCaseReport): string {
  const lines: string[] = [];

  lines.push(`--- Test Case: ${report.name} ---`);
  if (report.description) {
    lines.push(`  ${report.description}`);
  }
  if (report.params && Object.keys(report.params).length > 0) {
    lines.push(`  Params: ${JSON.stringify(report.params)}`);
  }

  for (const nodeReport of report.results) {
    lines.push(renderNodeReportText(nodeReport));
  }

  const { total, passed, failed } = report.summary;
  lines.push(`  Assertions: ${passed}/${total} passed${failed > 0 ? `, ${failed} failed` : ""}`);
  lines.push(`  Status: ${report.status.toUpperCase()}`);
  lines.push(`  Duration: ${report.duration}ms`);

  return lines.join("\n");
}

export function formatText(report: TestReport): string {
  const lines: string[] = [];

  lines.push(`=== Workflow Test: ${report.workflowId} ===`);
  lines.push(`Version: ${report.version}`);
  lines.push(`Timestamp: ${report.timestamp}`);
  lines.push("");

  if (report.testCases.length === 0) {
    lines.push("Warning: No test cases defined");
    lines.push("");
    lines.push("Summary: 0 passed, 0 failed (0 total)");
    return lines.join("\n");
  }

  for (const testCase of report.testCases) {
    lines.push(renderTestCaseText(testCase));
    lines.push("");
  }

  const { total, passed, failed } = report.summary;
  lines.push(`Summary: ${passed} passed, ${failed} failed (${total} total)`);

  return lines.join("\n");
}

// ── JSON Format (6.3, 6.4, 6.5) ──

export function formatJson(report: TestReport): string {
  return JSON.stringify(report, null, 2);
}

// ── Report Aggregation (6.5) ──

export function buildTestReport(
  workflowId: string,
  version: string,
  testCaseReports: TestCaseReport[],
): TestReport {
  const total = testCaseReports.reduce((sum, r) => sum + r.summary.total, 0);
  const passed = testCaseReports.reduce((sum, r) => sum + r.summary.passed, 0);
  const failed = testCaseReports.reduce((sum, r) => sum + r.summary.failed, 0);

  const status = testCaseReports.some((r) => r.status === "error")
    ? "error"
    : testCaseReports.some((r) => r.status === "failed")
      ? "failed"
      : "passed";

  return {
    workflowId,
    version,
    timestamp: new Date().toISOString(),
    testCases: testCaseReports,
    summary: { total, passed, failed },
    status,
  };
}