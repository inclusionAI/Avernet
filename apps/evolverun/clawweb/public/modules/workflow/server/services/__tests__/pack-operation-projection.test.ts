// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { stringify } from 'yaml';
import { candidateProjection, requestIdentity } from '../pack-operation-service.js';
const spec = { id: 'flow', title: 'Projection', nodes: [
  { id: 'start', type: 'start', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', type: 'end', executor: { type: 'end' }, dependsOn: ['start'] },
] };
const content = stringify(spec);
describe('candidate projection validation before any remote push', () => {
  it('retains exact YAML and extracts only the chosen workflow facade aliases', () => {
    const projection = candidateProjection('flow', content, stringify({ facades: [
      { command: 'flow', aliases: ['flow-alias'], defaultWorkflow: 'flow' },
      { command: 'other', defaultWorkflow: 'another' },
    ] }));
    expect(projection.facadeCommands).toEqual(['flow', 'flow-alias']);
    expect(JSON.parse(projection.specJson).content).toBe(content);
  });
  it.each(['nodes: [', 'id: flow\nnodes: []\n', 'id: wrong\nnodes: []\n'])('rejects invalid candidate %s', yaml => {
    expect(() => candidateProjection('flow', yaml, '{}')).toThrow();
  });
  it('rejects valid content belonging to another workflow', () => {
    expect(() => candidateProjection('other', content, '{}')).toThrow('WORKFLOW_ID_MISMATCH');
  });
  it.each([
    { facades: {} }, { facades: [null] }, { facades: [{ command: 'flow', defaultWorkflow: 2 }] },
    { facades: [{ command: 'flow', defaultWorkflow: 'flow', aliases: 'alias' }] },
    { facades: [{ command: '../flow', defaultWorkflow: 'flow' }] },
    { facades: [{ command: 'flow', aliases: [false], defaultWorkflow: 'flow' }] },
    { facades: [{ command: 'flow', aliases: ['flow'], defaultWorkflow: 'flow' }] },
  ])('rejects invalid command manifests before projection: %j', manifest => {
    expect(() => candidateProjection('flow', content, stringify(manifest))).toThrow('INVALID_MANIFEST');
  });
  it.each(['', 'short', 'x/y-not-allowed', 'x'.repeat(129), 12])('rejects invalid request identity %j', id => {
    expect(() => requestIdentity(id as string, () => 'generated')).toThrow('INVALID_REQUEST');
  });
  it('keeps a client request identity or generates one only when absent', () => {
    expect(requestIdentity('stable-request', () => 'unused')).toBe('stable-request');
    expect(requestIdentity(undefined, () => 'generated')).toBe('generated');
  });
});
