import type {
  Assertion,
  AssertionMatcher,
  AssertionResult,
  FlowState,
  OutputAssertion,
  StatusAssertion,
  VariableAssertion,
  TestCase,
  TestCaseReport,
  NodeExecutionReport,
  NodeStatus,
} from "../types.js";

// ── Dot-path resolution (5.8) ──

function resolveDotPath(obj: unknown, path: string): unknown {
  const parts = path.split(".");
  let current: unknown = obj;
  for (const part of parts) {
    if (current == null || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

// ── Assertion Evaluation ──

function evaluateEquals(actual: unknown, expected: unknown): { passed: boolean; message?: string } {
  if (JSON.stringify(actual) === JSON.stringify(expected)) return { passed: true };
  return {
    passed: false,
    message: `Expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
  };
}

function evaluateContains(actual: unknown, expected: string): { passed: boolean; message?: string } {
  if (typeof actual === "string") {
    return actual.includes(expected)
      ? { passed: true }
      : { passed: false, message: `Expected string to contain "${expected}", got "${actual}"` };
  }
  // For objects, check if any string value contains the substring
  if (actual != null && typeof actual === "object") {
    const str = JSON.stringify(actual);
    return str.includes(expected)
      ? { passed: true }
      : { passed: false, message: `Expected any value to contain "${expected}" in ${str}` };
  }
  return {
    passed: false,
    message: `Expected to contain "${expected}", got non-string value: ${JSON.stringify(actual)}`,
  };
}

function evaluateMatches(actual: unknown, pattern: string): { passed: boolean; message?: string } {
  let regex: RegExp;
  try {
    regex = new RegExp(pattern);
  } catch (err) {
    return { passed: false, message: `Invalid regex pattern "${pattern}": ${(err as Error).message}` };
  }
  const str = typeof actual === "string" ? actual : JSON.stringify(actual);
  if (regex.test(str)) return { passed: true };
  return { passed: false, message: `Expected "${str}" to match /${pattern}/` };
}

function evaluateType(actual: unknown, expectedType: string): { passed: boolean; message?: string } {
  const supportedTypes = new Set(["string", "number", "boolean", "object", "undefined"]);
  if (!supportedTypes.has(expectedType)) {
    return { passed: false, message: `Unsupported type assertion: "${expectedType}"` };
  }
  if (expectedType === "object") {
    const passed = actual !== null && typeof actual === "object";
    return passed ? { passed: true } : { passed: false, message: `Expected object, got ${actual === null ? "null" : typeof actual}` };
  }
  if (expectedType === "undefined") {
    return actual === undefined
      ? { passed: true }
      : { passed: false, message: `Expected undefined, got ${typeof actual}` };
  }
  const passed = typeof actual === expectedType;
  return passed ? { passed: true } : { passed: false, message: `Expected type ${expectedType}, got ${typeof actual}` };
}

function evaluateExists(actual: unknown, expectedExists: boolean): { passed: boolean; message?: string } {
  const actuallyExists = actual !== undefined && actual !== null;
  if (expectedExists) {
    return actuallyExists
      ? { passed: true }
      : { passed: false, message: "Expected value to exist, but it is undefined or null" };
  }
  return !actuallyExists
    ? { passed: true }
    : { passed: false, message: `Expected value to not exist, but got ${JSON.stringify(actual)}` };
}

// ── Output Assertion (5.2-5.6) ──

function evaluateOutputAssertion(
  assertion: OutputAssertion,
  flowState: FlowState,
  nodeIds: Set<string>,
): AssertionResult {
  const { nodeId, output } = assertion;

  if (!nodeIds.has(nodeId)) {
    return { type: "output", passed: false, message: `Node "${nodeId}" not found in workflow` };
  }

  const nodeOutput = flowState.workflowData[nodeId] as Record<string, unknown> | undefined;
  if (nodeOutput === undefined) {
    // Node may not have executed or failed
    const nodeState = flowState.nodeStates[nodeId];
    if (nodeState?.status === "skipped") {
      return { type: "output", passed: false, nodeId, message: `Node "${nodeId}" was skipped` };
    }
    return { type: "output", passed: false, nodeId, message: `No output for node "${nodeId}"` };
  }

  const results: AssertionResult[] = [];

  for (const [matcher, expected] of Object.entries(output)) {
    const result = evaluateMatcher(matcher as AssertionMatcher, expected, nodeOutput, nodeId);
    results.push(result);
  }

  // If multiple matchers on the same node, return the first failure or success
  const failed = results.find((r) => !r.passed);
  return failed ?? results[0] ?? { type: "output", passed: true, nodeId };
}

function evaluateMatcher(
  matcher: AssertionMatcher,
  expected: unknown,
  nodeOutput: Record<string, unknown>,
  nodeId: string,
): AssertionResult {
  switch (matcher) {
    case "equals":
      return { type: "output", matcher: "equals", path: `nodeOutput.${nodeId}`, expected, actual: nodeOutput, ...evaluateEquals(nodeOutput, expected) };
    case "contains":
      return { type: "output", matcher: "contains", path: `nodeOutput.${nodeId}`, expected: expected as string, actual: JSON.stringify(nodeOutput), ...evaluateContains(nodeOutput, expected as string) };
    case "matches": {
      const str = JSON.stringify(nodeOutput);
      return { type: "output", matcher: "matches", path: `nodeOutput.${nodeId}`, expected: expected as string, actual: str, ...evaluateMatches(nodeOutput, expected as string) };
    }
    case "type":
      return { type: "output", matcher: "type", path: `nodeOutput.${nodeId}`, expected: expected as string, actual: typeof nodeOutput, ...evaluateType(nodeOutput, expected as string) };
    case "exists": {
      const exists = expected as boolean;
      return { type: "output", matcher: "exists", path: `nodeOutput.${nodeId}`, expected: exists, actual: nodeOutput !== undefined, ...evaluateExists(nodeOutput, exists) };
    }
    case "status":
      // status matcher doesn't belong in output assertion, but handle gracefully
      return { type: "output", matcher: "status", passed: false, message: "status matcher not valid in output assertion" };
    default:
      return { type: "output", matcher, passed: false, message: `Unknown matcher: ${matcher}` };
  }
}

// ── Status Assertion (5.7) ──

function evaluateStatusAssertion(
  assertion: StatusAssertion,
  flowState: FlowState,
  nodeIds: Set<string>,
): AssertionResult {
  const { nodeId, status } = assertion;

  if (!nodeIds.has(nodeId)) {
    return { type: "status", passed: false, message: `Node "${nodeId}" not found in workflow` };
  }

  const nodeState = flowState.nodeStates[nodeId];
  const actualStatus = nodeState?.status ?? "pending";

  const passed = actualStatus === status;
  return {
    type: "status",
    matcher: "status",
    path: `nodeStates.${nodeId}.status`,
    expected: status,
    actual: actualStatus,
    passed,
    ...(passed ? {} : { message: `Expected node "${nodeId}" status "${status}", got "${actualStatus}"` }),
  };
}

// ── Variable Assertion (5.8) ──

function evaluateVariableAssertion(
  assertion: VariableAssertion,
  flowState: FlowState,
): AssertionResult {
  const { variable } = assertion;
  const actual = resolveDotPath(flowState, variable);

  // Check matches
  if (assertion.matches !== undefined) {
    const result = evaluateMatches(actual, assertion.matches as string);
    return { type: "variable", matcher: "matches", path: variable, expected: assertion.matches, actual, ...result };
  }

  // Check equals
  if (assertion.equals !== undefined) {
    const result = evaluateEquals(actual, assertion.equals);
    return { type: "variable", matcher: "equals", path: variable, expected: assertion.equals, actual, ...result };
  }

  // Check contains
  if (assertion.contains !== undefined) {
    const result = evaluateContains(actual, assertion.contains);
    return { type: "variable", matcher: "contains", path: variable, expected: assertion.contains, actual, ...result };
  }

  // Check type
  if (assertion.type !== undefined) {
    const result = evaluateType(actual, assertion.type);
    return { type: "variable", matcher: "type", path: variable, expected: assertion.type, actual, ...result };
  }

  // Check exists
  if (assertion.exists !== undefined) {
    const result = evaluateExists(actual, assertion.exists);
    return { type: "variable", matcher: "exists", path: variable, expected: assertion.exists, actual, ...result };
  }

  return { type: "variable", passed: false, message: `No matcher specified in variable assertion for "${variable}"` };
}

// ── Evaluate single assertion (5.9, 5.10) ──

function evaluateAssertion(
  assertion: Assertion,
  flowState: FlowState,
  nodeIds: Set<string>,
): AssertionResult {
  if ("variable" in assertion && assertion.variable) {
    return evaluateVariableAssertion(assertion as VariableAssertion, flowState);
  }
  if ("status" in assertion && assertion.status) {
    return evaluateStatusAssertion(assertion as StatusAssertion, flowState, nodeIds);
  }
  if ("output" in assertion && assertion.output) {
    return evaluateOutputAssertion(assertion as OutputAssertion, flowState, nodeIds);
  }
  return { type: "output", passed: false, message: "Unrecognized assertion shape" };
}

// ── Evaluate all assertions for a test case (5.11) ──

export function evaluateAssertions(
  flowState: FlowState,
  testAssertions: Assertion[],
): AssertionResult[] {
  const nodeIds = new Set(Object.keys(flowState.nodeStates));
  return testAssertions.map((assertion) => evaluateAssertion(assertion, flowState, nodeIds));
}

// ── Evaluate test suite ──

export function evaluateTestCase(
  flowState: FlowState,
  testCase: TestCase,
  nodeReports: NodeExecutionReport[],
): TestCaseReport {
  const startMs = Date.now();
  const assertionResults = evaluateAssertions(flowState, testCase.assertions);

  // Attach assertion results to the corresponding node reports
  for (const result of assertionResults) {
    const nodeId = result.nodeId;
    if (nodeId) {
      const report = nodeReports.find((r) => r.nodeId === nodeId);
      if (report) {
        report.assertions.push(result);
      }
    }
  }

  const passed = assertionResults.every((r) => r.passed);
  const failedCount = assertionResults.filter((r) => !r.passed).length;

  return {
    name: testCase.name,
    description: testCase.description,
    params: testCase.params,
    status: passed ? "passed" : "failed",
    duration: Date.now() - startMs,
    results: nodeReports,
    summary: {
      total: assertionResults.length,
      passed: assertionResults.length - failedCount,
      failed: failedCount,
    },
  };
}