type LineRange = [number, number];
export type SessionReadCoverage = {
  session_id: string;
  scope: "provided_records_only";
  provided_ranges: LineRange[];
  missing_ranges: LineRange[];
};

/** Facts about records provided by successful tools, not full history or model comprehension. */
export function addSessionReadCoverage(current: SessionReadCoverage[], value: unknown): SessionReadCoverage[] {
  const result = value as { session_id?: unknown; messages?: unknown; failure_context?: unknown } | null;
  if (!result || typeof result.session_id !== "string" || !result.session_id) return current;
  const ranges: LineRange[] = [...(current.find(item => item.session_id === result.session_id)?.provided_ranges ?? [])];
  for (const records of [result.messages, result.failure_context]) {
    if (!Array.isArray(records)) continue;
    for (const item of records) {
      if (Number.isSafeInteger(item?.line_no) && item.line_no > 0) ranges.push([item.line_no, item.line_no]);
    }
  }
  if (!ranges.length) return current;
  ranges.sort((a, b) => a[0] - b[0]);
  const provided: LineRange[] = [], missing: LineRange[] = [];
  for (const [start, end] of ranges) {
    const previous = provided.at(-1);
    if (previous && start <= previous[1] + 1) previous[1] = Math.max(previous[1], end);
    else provided.push([start, end]);
  }
  for (let i = 1; i < provided.length; i++) missing.push([provided[i - 1][1] + 1, provided[i][0] - 1]);
  return [...current.filter(item => item.session_id !== result.session_id), {
    session_id: result.session_id, scope: "provided_records_only", provided_ranges: provided, missing_ranges: missing,
  }];
}

export function sessionReadCoverage(calls: Iterable<{ toolName: string; status: string; result: unknown }>): SessionReadCoverage[] {
  let coverage: SessionReadCoverage[] = [];
  for (const call of calls) {
    if (call.toolName === "session_read" && call.status === "succeeded") coverage = addSessionReadCoverage(coverage, call.result);
  }
  return coverage;
}

export function boundedSessionReadCoverage(coverage: SessionReadCoverage[]) {
  return coverage.map(item => ({ ...item, provided_ranges: item.provided_ranges.slice(0, 40), missing_ranges: item.missing_ranges.slice(0, 40),
    ...(item.provided_ranges.length > 40 || item.missing_ranges.length > 40 ? { ranges_truncated: true } : {}) }));
}
