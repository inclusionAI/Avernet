// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { parse, stringify } from 'yaml';
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
    expect(JSON.parse(projection.specJson)).toEqual({ ...spec, content });
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

it('preserves the validated YAML document without validator normalization', () => {
  const yaml = [
    'id: flow',
    'version: 2',
    'title: "  Flow  "',
    'config: null',
    'nodes:',
    '  - id: start',
    '    title: Start',
    '    phase: null',
    '    executor:',
    '      type: start',
    ''].join('\n')
  const projection = candidateProjection('flow', yaml, '{}')
  const history = JSON.parse(projection.specJson) as Record<string, unknown>
  expect(history.version).toBe(2)
  expect(history.title).toBe('  Flow  ')
  expect(history.config).toBeNull()
  expect((history.nodes as Array<Record<string, unknown>>)[0].phase).toBeNull()
  expect(history.content).toBe(yaml)
  expect(Object.hasOwn(history, '__clawweb_snapshot_v1')).toBe(false)
})

it('rejects an unsafe JSON number before any history projection is produced', () => {
  const yaml = 'id: flow\nnodes:\n  - id: start\n    executor: { type: start }\nlarge: 9007199254740993\n'
  expect(() => candidateProjection('flow', yaml, '{}')).toThrow('INVALID_WORKFLOW')
})


it.each(['user payload', null, false, { nested: 1 }])('archives exact deployed YAML including authored content: %j', content => {
  const authored = { ...spec, content }
  const yaml = stringify(authored)
  const history = JSON.parse(candidateProjection('flow', yaml, '{}').specJson)
  expect(history.content).toBe(yaml)
  expect(parse(history.content)).toEqual(authored)
  expect(Object.hasOwn(history, '__clawweb_snapshot_v1')).toBe(false)
})

it.each([null, false, 'user marker', { version: 1, contentSource: 'generated' }])('archives an authored marker collision without altering it: %j', marker => {
  const authored = { ...spec, __clawweb_snapshot_v1: marker, content: 'user content' }
  const yaml = stringify(authored)
  const history = JSON.parse(candidateProjection('flow', yaml, '{}').specJson)
  expect(history.content).toBe(yaml)
  expect(parse(history.content)).toEqual(authored)
  expect(history.__clawweb_snapshot_v1).toEqual(marker)
})


it('accepts legacy null dependsOn without normalizing the archived document', () => {
  const yaml = 'id: flow\nnodes:\n  - id: start\n    executor: { type: start }\n    dependsOn: null # legacy\ncustom: { preserved: true }\n';
  const history = JSON.parse(candidateProjection('flow', yaml, '{}').specJson);
  expect(history.nodes[0].dependsOn).toBeNull();
  expect(history.custom).toEqual({ preserved: true });
  expect(history.content).toBe(yaml);
});

it('still rejects an unsafe number when a legacy null dependency is present', () => {
  const yaml = 'id: flow\nnodes:\n  - id: start\n    executor: { type: start }\n    dependsOn: null\nlarge: 9007199254740993\n';
  expect(() => candidateProjection('flow', yaml, '{}')).toThrow('INVALID_WORKFLOW');
});


it.each(['null', '~', ''])('accepts legacy YAML null dependency spelling %j', spelling => {
  const yaml = `id: flow\nnodes:\n  - id: start\n    executor: { type: start }\n    dependsOn: ${spelling} # retained\n`;
  const history = JSON.parse(candidateProjection('flow', yaml, '{}').specJson);
  expect(history.nodes[0].dependsOn).toBeNull();
  expect(history.content).toBe(yaml);
});

it.each([
  'dependsOn: false',
  'dependsOn: [42]',
  'dependsOn: [missing]',
  'dependsOn: null\n    triggerRule: invalid',
  'dependsOn: null\n    dependsOn: null',
  'dependsOn: &legacy null',
])('does not weaken other validation with %s', dependency => {
  const yaml = `id: flow\nnodes:\n  - id: start\n    executor: { type: start }\n    ${dependency}\n`;
  expect(() => candidateProjection('flow', yaml, '{}')).toThrow('INVALID_WORKFLOW');
});

it('legacy projection accepts bounded aliases and false dependencies while strict projection rejects them', () => {
  const yaml = 'id: flow\ncustom: &user { message: hello }\ncopied: *user\nnodes:\n  - id: start\n    executor: { type: start }\n    dependsOn: false\n';
  const history = JSON.parse(candidateProjection('flow', yaml, '{}', 'legacy-autoheal').specJson);
  expect(history.content).toBe(yaml);
  expect(history.custom).toEqual(history.copied);
  expect(history.nodes[0].dependsOn).toBe(false);
  expect(() => candidateProjection('flow', yaml, '{}')).toThrow('INVALID_WORKFLOW');
});

it.each([
  'custom: 9007199254740993', 'custom: 0.10000000000000001',
  'custom: { a: 1, a: 2 }', 'custom: &cycle [*cycle]',
  `custom: &x [${Array(110).fill('1').join(',')} ]\ncopy: [${Array(110).fill('*x').join(',')}]`,
])('legacy projection rejects unsafe YAML: %s', extra => {
  const yaml = `id: flow\nnodes:\n  - id: start\n    executor: {type: start}\n    dependsOn: false\n${extra}\n`;
  expect(() => candidateProjection('flow', yaml, '{}', 'legacy-autoheal')).toThrow('INVALID_WORKFLOW');
});


it('supports a bounded alias as a string mapping key without losing the original YAML', () => {
  const yaml = 'id: flow\nkey: &k custom\nextra:\n  ? *k\n  : kept\nnodes:\n  - id: start\n    executor: {type: start}\n    dependsOn: false\n';
  const history = JSON.parse(candidateProjection('flow', yaml, '{}', 'legacy-autoheal').specJson);
  expect(history.extra).toEqual({ custom: 'kept' });
  expect(history.content).toBe(yaml);
});

it('rejects duplicate mapping keys introduced through an alias', () => {
  const yaml = 'id: flow\nkey: &k custom\nextra:\n  ? *k\n  : 1\n  custom: 2\nnodes:\n  - id: start\n    executor: {type: start}\n';
  expect(() => candidateProjection('flow', yaml, '{}', 'legacy-autoheal')).toThrow('INVALID_WORKFLOW');
});

it('checks the legacy normalized workflow identity', () => {
  const yaml = 'id: " flow "\nnodes:\n  - id: start\n    executor: {type: start}\n';
  expect(JSON.parse(candidateProjection('flow', yaml, '{}', 'legacy-autoheal').specJson).id).toBe(' flow ');
});


it('preserves the pre-existing strict Editor allowance for top-level null dependencies', () => {
  const yaml = 'id: flow\nnodes:\n  - id: start\n    executor: {type: start}\n    dependsOn: null\n';
  const history = JSON.parse(candidateProjection('flow', yaml, '{}').specJson);
  expect(history.nodes[0].dependsOn).toBeNull();
  expect(history.content).toBe(yaml);
});

it('projects legacy numeric keys, !!str and exactly round-trippable large numbers', () => {
  const yaml = 'id: flow\nconfig: {200: ok}\nlabel: !!str 200\nlarge: 10000000000000000\nnodes:\n  - id: start\n    executor: {type: start}\n';
  const result = JSON.parse(candidateProjection('flow', yaml, '{}', 'legacy-autoheal').specJson);
  expect(result.config).toEqual({ '200': 'ok' });
  expect(result.label).toBe('200');
  expect(result.large).toBe(10000000000000000);
  expect(result.content).toBe(yaml);
});

it.each(['config: {200: ok, "200": collision}', 'config: {true: one, "true": two}',
  'large: 10000000000000001', 'config: {10000000000000001: bad}'])('rejects legacy key collisions and rounding: %s', item => {
  const yaml = `id: flow\n${item}\nnodes:\n  - id: start\n    executor: {type: start}\n`;
  expect(() => candidateProjection('flow', yaml, '{}', 'legacy-autoheal')).toThrow('INVALID_WORKFLOW');
});

it('does not impose a 1 MiB limit on the JSON encoding of a smaller legacy YAML', () => {
  const yaml = `id: flow\nnodes:\n  - id: start\n    executor: {type: start}\ntext: '${'\\'.repeat(550_000)}'\n`;
  expect(Buffer.byteLength(yaml)).toBeLessThan(1024 * 1024);
  expect(Buffer.byteLength(JSON.stringify(parse(yaml)))).toBeGreaterThan(1024 * 1024);
  const history = JSON.parse(candidateProjection('flow', yaml, '{}', 'legacy-autoheal').specJson);
  expect(history.text).toBe('\\'.repeat(550_000));
  expect(history.content).toBe(yaml);
});

it.each([
  ['!!int 2', 2], ['!!float 0.5', 0.5], ['!!bool true', true], ['!!null null', null],
  ['!!map {a: 1}', { a: 1 }], ['!!seq [a, b]', ['a', 'b']],
])('legacy projection accepts lossless standard YAML tag %s', (value, expected) => {
  const yaml = `id: flow\nconfig: {value: ${value}}\nnodes:\n  - id: start\n    executor: {type: start}\n`;
  const history = JSON.parse(candidateProjection('flow', yaml, '{}', 'legacy-autoheal').specJson);
  expect(history.config.value).toEqual(expected);
  expect(history.content).toBe(yaml);
});

it.each(['!!float 0.10000000000000001', '!!int 9007199254740993', '!!float .inf', '!!set {a: null}'])('legacy projection rejects unsafe tagged YAML %s', value => {
  const yaml = `id: flow\nconfig: {value: ${value}}\nnodes:\n  - id: start\n    executor: {type: start}\n`;
  expect(() => candidateProjection('flow', yaml, '{}', 'legacy-autoheal')).toThrow('INVALID_WORKFLOW');
});
