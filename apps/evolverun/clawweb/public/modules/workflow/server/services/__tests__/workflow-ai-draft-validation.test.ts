import { randomUUID } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { parse as parseYaml } from 'yaml';
import { aiDraftWorkflow, validateAiDraftRequest, validateAiDraftOutput } from '../workflow-ai-draft-validation.js';
import { overlayYaml } from './workflow-ai-draft-fixtures.js';

// Generic workflow corpus: no experiment, Pack, path or external fixture dependency.
const corpus = [
  { name: 'numeric version and unsaved source', workflowId: 'flow', yaml: overlayYaml, expectedVersion: 7, expectedNodeIds: ['n'] },
  { name: 'YAML 1.2 non-boolean node IDs', workflowId: 'flow.v2',
    yaml: 'id: flow.v2\nversion: "release-7"\nnodes:\n  - id: on\n    executor: {type: agent}\n  - id: off\n    executor: {type: agent}\n',
    expectedVersion: 'release-7', expectedNodeIds: ['on', 'off'] },
  { name: 'Unicode prompt and block text', workflowId: 'flow',
    yaml: 'id: flow\nversion: 7\ndescription: |\n  保留精确文本\n  Second line\nnodes:\n  - id: n\n    executor: {type: agent, prompt: "保持原样 😀"}\n',
    expectedVersion: 7, expectedNodeIds: ['n'] },
  { name: 'bounded non-cyclic aliases', workflowId: 'flow',
    yaml: 'id: flow\nversion: 7\nnodes:\n  - id: first\n    executor: &agent {type: agent, prompt: "Keep aliases"}\n  - id: second\n    executor: *agent\n',
    expectedVersion: 7, expectedNodeIds: ['first', 'second'] },
];
describe('Workflow AI draft generic YAML corpus and invariants', () => {
  it.each(corpus)('accepts exact YAML 1.2 source: $name', example => {
    const workflow = aiDraftWorkflow(example.yaml, example.workflowId);
    expect(workflow).toEqual(parseYaml(example.yaml));
    expect(workflow.version).toEqual(example.expectedVersion);
    expect((workflow.nodes as Array<{ id: string }>).map(node => node.id)).toEqual(example.expectedNodeIds);
    expect(() => validateAiDraftRequest(example.workflowId, { requestId: randomUUID(), mode: 'modify', prompt: 'Improve this workflow', currentYaml: example.yaml })).not.toThrow();
    expect(() => validateAiDraftOutput(example.yaml, example.yaml, example.workflowId)).not.toThrow();
  });
  it.each(['.nan', '.inf', '-.inf', '0', '-1', 'null', 'false', '""'])(
    'rejects invalid workflow version without JSON scalar coercion: %s', version => {
      const input = overlayYaml.replace('version: 7', `version: ${version}`);
      expect(() => aiDraftWorkflow(input, 'flow')).toThrow();
    });
  it('preserves the exact workflow version value and type in output', () => {
    for (const version of ['8', '"7"']) {
      expect(() => validateAiDraftOutput(overlayYaml.replace('version: 7', `version: ${version}`), overlayYaml, 'flow')).toThrow();
    }
    expect(() => validateAiDraftOutput(overlayYaml.replace('id: flow', 'id: other'), overlayYaml, 'flow')).toThrow();
  });
  it.each([
    overlayYaml + 'version: 8\n', overlayYaml + 'custom: &recursive [*recursive]\n',
    overlayYaml + '? [complex, key]\n: value\n', overlayYaml + 'custom: .inf\n', overlayYaml + 'custom: !!binary abc=\n',
    overlayYaml + 'custom: \0\n', overlayYaml + '\ud800',
  ])('rejects ambiguous, recursive, non-JSON or invalid UTF8 input', yaml => {
    expect(() => aiDraftWorkflow(yaml, 'flow')).toThrow();
  });
  it('bounds aliases and deep structures before workflow schema validation', () => {
    const aliases = overlayYaml + 'custom: &text "' + 'x'.repeat(20_000) + '"\nexpanded: [' + Array(60).fill('*text').join(', ') + ']\n';
    expect(Buffer.byteLength(aliases)).toBeLessThan(100_000);
    expect(() => aiDraftWorkflow(aliases, 'flow')).toThrow();
    expect(() => aiDraftWorkflow(overlayYaml + 'custom: ' + '['.repeat(60) + '0' + ']'.repeat(60), 'flow')).toThrow();
  });
  it('charges exact UTF8 YAML bytes and Unicode prompt code points', () => {
    const exact = overlayYaml + '# ' + 'x'.repeat(100_000 - Buffer.byteLength(overlayYaml) - 2);
    expect(() => aiDraftWorkflow(exact, 'flow')).not.toThrow();
    expect(() => aiDraftWorkflow(exact + 'x', 'flow')).toThrow();
    const request = { requestId: randomUUID(), mode: 'create' as const, prompt: '😀'.repeat(4000), currentYaml: overlayYaml };
    expect(() => validateAiDraftRequest('flow', request)).not.toThrow();
    expect(() => validateAiDraftRequest('flow', { ...request, prompt: request.prompt + '😀' })).toThrow();
  });
});
