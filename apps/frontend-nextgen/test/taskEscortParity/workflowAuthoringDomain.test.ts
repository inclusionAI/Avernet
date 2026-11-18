import {
  assertWorkflowAuthoringBaseline, authoringJson, authoringSpec, authoringWorkflowId,
  authoringYamlText, buildBlankWorkflowAuthoringSpec, parseWorkflowAuthoringYaml,
  restoreWorkflowAuthoringLayout, workflowAuthoringSignature, workflowAuthoringYaml,
} from '@/domain/taskEscortParity/workflowAuthoring';
import { authoringFileChanges } from '@/domain/taskEscortParity/workflowAuthoringPack';

const spec = {
  id: 'flow', title: '真实流程', version: '2', custom: { enabled: false, values: [null, 1] },
  nodes: [{ id: 'first', executor: { type: 'cli-script', command: 'scripts/run.sh', custom: { x: 1 } },
    _x: 42, _y: 70, extension: { nested: ['a'] } },
  { id: 'done', dependsOn: ['first'], executor: { type: 'done' } }],
};

it('includes executable permission changes in the complete publish review', () => {
  const file = { path: 'scripts/run.sh', contentBase64: 'ZWNobyBvaw==', mode: '100644' as const };
  expect(authoringFileChanges([file], [{ ...file, mode: '100755' }])).toEqual([{
    path: file.path, operation: 'replace', before: 'echo ok', after: 'echo ok',
    beforeMode: '100644', afterMode: '100755',
  }]);
});

it('uses the real blank workflow starter and never claims an empty nodes spec is valid', () => {
  expect(buildBlankWorkflowAuthoringSpec('new-flow', '新工作流')).toEqual({ id: 'new-flow', title: '新工作流', version: '1.0.0',
    nodes: [{ id: 'new-flow_start', title: '开始', executor: { type: 'done' } }] });
  expect(() => authoringSpec({ ...spec, nodes: [] })).toThrow(/节点/);
});
it('round-trips every unknown top-level, node and executor field without mutating input', () => {
  const result = parseWorkflowAuthoringYaml(workflowAuthoringYaml(spec));
  expect(result).toEqual(spec);
  expect(result).not.toBe(spec);
  expect(result.nodes[0]).not.toBe(spec.nodes[0]);
});
it('supports the real onboarding workflow wrapper while retaining its extension keys', () => {
  const yaml = 'workflow:\n  id: draft\n  title: draft title\n  version: 4\n  extension: {retained: true}\nnodes:\n- id: done\n  executor: {type: done}\ncustom: [false, null]\n';
  expect(parseWorkflowAuthoringYaml(yaml)).toEqual(expect.objectContaining({ id: 'draft', title: 'draft title', version: 4,
    workflow: { id: 'draft', title: 'draft title', version: 4, extension: { retained: true } },
    custom: [false, null], extension: { retained: true } }));
});
it.each(['', '__platform__', '../other', 'space id', 'a/b', 'a\\b', 'a..b', 'x'.repeat(129)])('rejects unsafe workflow IDs: %s', id => {
  expect(() => authoringWorkflowId(id)).toThrow();
});
it.each([
  null, [], { ...spec, id: 'other' }, { ...spec, title: '' }, { ...spec, nodes: [spec.nodes[0], spec.nodes[0]] },
  { ...spec, nodes: [{ id: 'bad', executor: {} }] },
  { ...spec, nodes: [{ id: 'bad', executor: { type: 'done' }, dependsOn: ['missing'] }] },
  { ...spec, nodes: [{ id: 'bad', executor: { type: 'done' }, dependsOn: [false] }] },
  { ...spec, nodes: [{ id: 'a', executor: { type: 'done' }, dependsOn: ['b'] }, { id: 'b', executor: { type: 'done' }, dependsOn: ['a'] }] },
  { ...spec, nodes: [{ id: 'bad', executor: { type: 'done' }, onResult: [{ target: 'missing' }] }] },
])('rejects malformed, cross-scope or cyclic specs: %j', value => {
  expect(() => authoringSpec(value, 'flow')).toThrow();
});
it('keeps opaque object-form runtime branches instead of inventing a narrower protocol', () => {
  const branches = { if: '${result.ok}', then: 'external-extension', plugin: { arbitrary: true } };
  expect(authoringSpec({ ...spec, nodes: [{ ...spec.nodes[0], onResult: branches }] }).nodes[0].onResult).toEqual(branches);
});
it.each(['id: a\nid: b\nnodes: []', '!!js/function function(){}', '```yaml\nid: flow\n```', '- one\n- two', 'a: &a {b: *a}'])('rejects duplicate, non-data, fenced or recursive YAML: %s', yaml => {
  expect(() => parseWorkflowAuthoringYaml(yaml)).toThrow();
});
it('bounds YAML by UTF-8 bytes, rejecting multi-byte prompts without truncating them', () => {
  expect(() => authoringYamlText('中'.repeat(33334))).toThrow(/100000/);
  expect(authoringYamlText('中'.repeat(33333))).toHaveLength(33333);
});
it.each([NaN, Infinity, undefined, () => 1, new Date(), 9007199254740992])('rejects lossy JSON data: %s', value => {
  expect(() => authoringJson({ value })).toThrow();
});
it('limits alias expansion and recursive JSON before serialization', () => {
  const cyclic: Record<string, unknown> = {}; cyclic.self = cyclic;
  expect(() => authoringJson(cyclic)).toThrow(/循环/);
  const data = Array(100001).fill(null);
  expect(() => authoringJson(data)).toThrow(/安全限制/);
});
it('uses a formatting-independent signature including original layout and unknown fields', () => {
  expect(workflowAuthoringSignature(spec)).toBe(workflowAuthoringSignature({ nodes: spec.nodes, version: '2', custom: spec.custom, title: spec.title, id: spec.id }));
  expect(workflowAuthoringSignature(spec)).not.toBe(workflowAuthoringSignature({ ...spec, nodes: [{ ...spec.nodes[0], _x: 99 }, spec.nodes[1]] }));
});
it('rejects AI ID/version changes and restores only genuine existing-node layout', () => {
  expect(() => assertWorkflowAuthoringBaseline({ ...spec, id: 'other' }, spec)).toThrow(/ID/);
  expect(() => assertWorkflowAuthoringBaseline({ ...spec, version: '3' }, spec)).toThrow(/版本/);
  expect(() => assertWorkflowAuthoringBaseline({ ...spec, version: 2 }, spec)).not.toThrow();
  const next = restoreWorkflowAuthoringLayout({ ...spec, nodes: [{ ...spec.nodes[0], _x: 999 }, { id: 'new', _x: 500, executor: { type: 'done' } }] }, spec);
  expect(next.nodes[0]._x).toBe(42);
  expect(next.nodes[1]).not.toHaveProperty('_x');
  expect(spec.nodes[0]._x).toBe(42);
});
