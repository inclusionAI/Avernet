/** Same pinned vectors as claw-validation; no provider, network or recovery execution. */
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { ACTION_POLICY_VERSION, deriveAlert, eligibleForRecovery, recoverySkipReason, type Tags } from '../action-policy.js';
import { TC_CODES, TAXONOMY_VERSION } from '../tc-catalog.js';
import { DIAGNOSIS_V4_VERSION, DIAGNOSIS_VERSION } from '../contracts.js';
import { parseDiagnosis } from '../validation.js';
const bytes = readFileSync('server/fixtures/monitoring/action-policy-v1.json');
const fixture = JSON.parse(bytes.toString());
const base = JSON.parse(readFileSync('server/fixtures/monitoring/alert.json', 'utf8'));
function wire(c: {success: boolean; completed: boolean | null; correction: boolean | null; code: string | null; decision: string}) {
  return { ...base, schemaVersion: DIAGNOSIS_V4_VERSION, runtimeIdentity: null,
    decision: c.decision, handlerName: c.success ? c.completed ? 'COMPLETED' : 'NOT_COMPLETED' : null,
    humanIntervention: c.correction ?? false, tcFaultLabel: c.code, confidence: c.code ? .75 : null,
    businessProblemCategory: c.code ? '报错' : null, businessProblemSubtype: c.code ? 'tool_parse_error' : null };
}
it('pins the cross-repository fixture and all 67 codes', () => {
  expect(createHash('sha256').update(bytes).digest('hex')).toBe('c93d606b4a1b18d571e7f7c1eb2b2568ab6c72a5b1ec3c94df282b272a633bd9');
  expect(fixture.policy_version).toBe(ACTION_POLICY_VERSION);
  expect(fixture.taxonomy_version).toBe(TAXONOMY_VERSION);
  expect([...TC_CODES].sort()).toEqual(fixture.codes);
});
describe.each(fixture.cases)('$id', c => {
  it('derives both Actions and validates the complete wire', () => {
    const tags: Tags | null = c.success ? {completed: c.completed, correction: c.correction, code: c.code} : null;
    expect(deriveAlert(tags)).toBe(c.decision);
    expect(eligibleForRecovery(tags)).toBe(c.recovery);
    const event = parseDiagnosis(wire(c), base.eventId);
    expect(event.tcFaultLabel).toBe(c.code);
    expect(recoverySkipReason(event) === null).toBe(c.recovery);
  });
});
it.each([
  {decision: 'PASS'}, {handlerName: null}, {handlerName: 'custom'}, {humanIntervention: 'false'},
  {tcFaultLabel: 'TC.FAKE.CODE'}, {tcFaultLabel: 'TC.MCP.UNKNOWN'}, {confidence: null}, {confidence: Number.NaN},
  {confidence: 1.1}, {confidence: true}, {runtimeIdentity: []},
])('rejects invalid/mismatched new tags %j', patch => {
  expect(() => parseDiagnosis({...wire(fixture.cases[6]), ...patch}, base.eventId)).toThrow();
});
it('requires every v4 field, including explicit nullable attribution', () => {
  for (const key of Object.keys(wire(fixture.cases[6]))) {
    const value = wire(fixture.cases[6]); delete value[key];
    expect(() => parseDiagnosis(value, base.eventId)).toThrow();
  }
});
it('retains v2 rejection of PASS with a code, without reinterpreting legacy ALERT', () => {
  const value = wire(fixture.cases[2]); delete value.runtimeIdentity;
  expect(() => parseDiagnosis({...value, schemaVersion: DIAGNOSIS_VERSION}, base.eventId)).toThrow();
  const legacy = parseDiagnosis({...base, handlerName: 'COMPLETED'}, base.eventId);
  expect(legacy.decision).toBe('ALERT');
  expect(recoverySkipReason(legacy)).toBe('legacy_policy');
});
