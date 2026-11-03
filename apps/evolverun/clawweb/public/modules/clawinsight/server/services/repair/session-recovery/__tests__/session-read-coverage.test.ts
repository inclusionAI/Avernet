import { expect, it } from "vitest";
import { addSessionReadCoverage, boundedSessionReadCoverage, sessionReadCoverage } from "../session-read-coverage.js";

const page = (session_id: string, lines: unknown[]) => ({ session_id, messages: lines.map(line_no => ({ line_no })) });

it("merges provided intervals, isolates physical sessions and ignores failed or unrelated reads", () => {
  const calls = [
    { toolName: "session_read", status: "succeeded", result: page("original", [1, 2, 5, 6, 0, -1, "3"]) },
    { toolName: "session_read", status: "failed", result: page("original", [3, 4]) },
    { toolName: "trajectory_read", status: "succeeded", result: page("original", [3, 4]) },
    { toolName: "session_read", status: "succeeded", result: page("other", [3, 4]) },
  ];
  const coverage = sessionReadCoverage(calls);
  expect(coverage).toEqual([
    { session_id: "original", scope: "provided_records_only", provided_ranges: [[1, 2], [5, 6]], missing_ranges: [[3, 4]] },
    { session_id: "other", scope: "provided_records_only", provided_ranges: [[3, 4]], missing_ranges: [] },
  ]);
  const before = structuredClone(coverage);
  expect(addSessionReadCoverage(coverage, { ...page("original", [2, 3]), failure_context: [{ line_no: 4 }] })[1])
    .toEqual({ session_id: "original", scope: "provided_records_only", provided_ranges: [[1, 6]], missing_ranges: [] });
  expect(coverage).toEqual(before);
});

it("does not infer unread history before a tail page and bounds display without mutating the audit projection", () => {
  const coverage = addSessionReadCoverage([], page("original", Array.from({ length: 100 }, (_, i) => 1000 + i * 2)));
  expect(coverage[0].provided_ranges[0]).toEqual([1000, 1000]);
  expect(coverage[0].missing_ranges[0]).toEqual([1001, 1001]);
  const [display] = boundedSessionReadCoverage(coverage);
  expect(display.provided_ranges).toHaveLength(40); expect(display.missing_ranges).toHaveLength(40);
  expect(display.ranges_truncated).toBe(true);
  expect(coverage[0].provided_ranges).toHaveLength(100);
  expect(display).not.toHaveProperty("complete");
});
