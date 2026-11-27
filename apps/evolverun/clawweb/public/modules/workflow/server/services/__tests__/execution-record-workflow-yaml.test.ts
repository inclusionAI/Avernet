import { describe, expect, it, vi } from 'vitest';
import { parse, parseDocument, stringify, visit } from 'yaml';
import {
  ExecutionRecordWorkflowDocumentError, executionRecordWorkflowYaml,
  parseExecutionRecordWorkflowJson, readExecutionRecordWorkflowSpec,
} from '../execution-record-workflow-yaml.js';

const spec = { id: 'wf', nodes: [] };
function expectMissing(call: () => unknown) {
  expect(call).toThrow(ExecutionRecordWorkflowDocumentError);
  try { call(); } catch (error) { expect(error).toMatchObject({ reason: 'SNAPSHOT_MISSING' }); }
}
describe('execution record YAML parsing budgets', () => {
  it('accepts valid semantically equivalent structured JSON without running current runtime validators', () => {
    const old = { ...spec, nodes: [{ id: 'legacy-step', executor: { type: 'historical-executor' } }], extension: { content: 'business data' } };
    expect(parse(executionRecordWorkflowYaml(readExecutionRecordWorkflowSpec(JSON.stringify(old)), 'wf'))).toEqual(old);
  });

  it('accepts the inclusive 1 MiB source JSON and raw YAML boundaries, but rejects one byte more', () => {
    const limit = 1024 * 1024;
    const json = JSON.stringify({ ...spec, value: '' });
    const atLimit = JSON.stringify({ ...spec, value: 'x'.repeat(limit - Buffer.byteLength(json)) });
    expect(Buffer.byteLength(atLimit)).toBe(limit);
    expect(readExecutionRecordWorkflowSpec(atLimit).id).toBe('wf');
    expectMissing(() => readExecutionRecordWorkflowSpec(atLimit + ' '));
    const prefix = 'id: wf\nnodes: []\nvalue: ';
    const yaml = prefix + 'x'.repeat(limit - Buffer.byteLength(prefix));
    expect(executionRecordWorkflowYaml({ content: yaml }, 'wf')).toBe(yaml);
    expectMissing(() => executionRecordWorkflowYaml({ content: yaml + ' ' }, 'wf'));
    const base = stringify({ ...spec, value: 'x' }, { lineWidth: 0 });
    const value = 'x'.repeat(limit - Buffer.byteLength(base) + 1);
    expect(Buffer.byteLength(executionRecordWorkflowYaml({ ...spec, value }, 'wf'))).toBe(limit);
    expectMissing(() => executionRecordWorkflowYaml({ ...spec, value: value + 'x' }, 'wf'));
  });

  it.each([64 * 1024, 8 * 1024 * 1024])('enforces the %s-byte entry budget before invoking JSON.parse', maxBytes => {
    const source = JSON.stringify({ value: 'x'.repeat(maxBytes) });
    const parseJson = vi.spyOn(JSON, 'parse');
    try {
      expectMissing(() => parseExecutionRecordWorkflowJson(source, undefined, maxBytes));
      expect(parseJson).not.toHaveBeenCalled();
    } finally { parseJson.mockRestore(); }
  });

  it('rejects source JSON, wrapper YAML and generated YAML over the 1 MiB byte limit', () => {
    const oversized = '大'.repeat(400_000);
    expectMissing(() => readExecutionRecordWorkflowSpec(JSON.stringify({ ...spec, value: oversized })));
    expectMissing(() => executionRecordWorkflowYaml({ content: `id: wf\nnodes: []\nvalue: ${oversized}` }, 'wf'));
    expectMissing(() => executionRecordWorkflowYaml({ ...spec, value: oversized }, 'wf'));
  });

  it('bounds inspected AST nodes at 100,000', () => {
    const base = { ...spec, values: [] as number[] };
    let count = 0;
    visit(parseDocument(JSON.stringify(base)), () => { count++; });
    const value = { ...base, values: Array.from({ length: 100_000 - count }, () => 0) };
    expect(readExecutionRecordWorkflowSpec(JSON.stringify(value))).toEqual(value);
    value.values.push(0);
    expectMissing(() => readExecutionRecordWorkflowSpec(JSON.stringify(value)));
  });

  it('bounds inspected AST path length at 100', () => {
    let nested: unknown = 0;
    const depth = () => {
      let maximum = 0;
      visit(parseDocument(JSON.stringify({ ...spec, nested })), (_key, _node, path) => { maximum = Math.max(maximum, path.length); });
      return maximum;
    };
    while (depth() < 100) nested = [nested];
    expect(depth()).toBe(100);
    const atLimit = JSON.stringify({ ...spec, nested });
    expect(readExecutionRecordWorkflowSpec(atLimit)).toEqual(JSON.parse(atLimit));
    nested = [nested];
    expectMissing(() => readExecutionRecordWorkflowSpec(JSON.stringify({ ...spec, nested })));
  });

  it('does not apply protected snapshot budgets/numeric restrictions to unrelated execution state', () => {
    const state = JSON.stringify({ workflowSnapshot: spec, workflowPin: { source: 'remote-snapshot' },
      unrelated: { largeInteger: 1e30, values: Array.from({ length: 100_001 }, () => 0) } });
    expect(parseExecutionRecordWorkflowJson(state, ['workflowSnapshot', 'workflowPin'])).toEqual(JSON.parse(state));
  });

  it.each(['id: wf\nnodes: []\nvalue: .nan\n', 'id: wf\nnodes: []\nvalue: -0\n',
    'id: wf\nnodes: []\nvalue: 9007199254740993\n', '%YAML 1.1\n---\nid: wf\nnodes: []\n'])
  ('rejects non-JSON-compatible or ambiguous YAML: %s', content => {
    expectMissing(() => executionRecordWorkflowYaml({ content }, 'wf'));
  });
});
