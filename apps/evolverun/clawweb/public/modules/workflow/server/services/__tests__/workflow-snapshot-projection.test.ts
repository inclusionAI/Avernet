// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { parse, stringify } from 'yaml';
import { compareWorkflowSnapshotYaml, projectWorkflowSnapshot, projectWorkflowSnapshotJson, WORKFLOW_SNAPSHOT_PROJECTION_VERSION } from '../workflow-snapshot-projection.js';

const doc = () => ({ id: 'flow', title: ' My flow ', content: 'user-authored content', nodes: [
  { id: 'first', executor: { type: 'agent', custom: { z: 2, a: 1 } }, extra: ['b', 'a'] },
  { id: 'second', executor: { type: 'agent' }, dependsOn: ['first'] },
], custom: { nested: true, value: null }, version: 2 });

describe('workflow snapshot projection v1', () => {
  it('deterministically preserves the complete input, including content and extensions', () => {
    const value = doc();
    const reordered = { version: 2, custom: { value: null, nested: true }, nodes: [
      { extra: ['b', 'a'], executor: { custom: { a: 1, z: 2 }, type: 'agent' }, id: 'first' },
      { dependsOn: ['first'], executor: { type: 'agent' }, id: 'second' },
    ], content: 'user-authored content', title: ' My flow ', id: 'flow' };
    const a = projectWorkflowSnapshot('flow', value);
    const b = projectWorkflowSnapshot('flow', reordered);
    expect(a.version).toBe(WORKFLOW_SNAPSHOT_PROJECTION_VERSION);
    expect(a.digest).toMatch(/^[a-f0-9]{64}$/);
    expect(a.digest).toBe(b.digest);
    expect(a.yaml).toBe(b.yaml);
    expect(parse(a.yaml)).toEqual(value);
    expect(JSON.parse(a.specJson)).toEqual(value);
    expect(value.content).toBe('user-authored content');
  });

  it('projects raw database JSON without permitting parse-time numeric or duplicate-key loss', () => {
    const source = JSON.stringify(doc());
    const fromText = projectWorkflowSnapshotJson('flow', source);
    expect(fromText).toEqual(projectWorkflowSnapshot('flow', doc()));
    expect(projectWorkflowSnapshotJson('flow', source.replace('\"version\":2', '\"version\":2.0')).specJson).toEqual(fromText.specJson);
    for (const json of [
      source.replace('\"version\":2', '\"version\":9007199254740993'),
      source.replace('\"version\":2', '\"version\":0.10000000000000001'),
      source.replace('\"version\":2', '\"version\":-0'),
      source.replace('\"id\":\"flow\"', '\"id\":\"flow\",\"id\":\"flow\"'),
      source.replace('\"nested\":true', '\"nested\":true,\"\\u006eested\":true'),
      'id: flow\nnodes: []',
      '',
    ]) expect(() => projectWorkflowSnapshotJson('flow', json)).toThrow();
    const stringDigits = { ...doc(), value: '9007199254740993' };
    expect(projectWorkflowSnapshotJson('flow', JSON.stringify(stringDigits)).specJson)
      .toEqual(projectWorkflowSnapshot('flow', stringDigits).specJson);
  });

  it('rejects long lossy decimal literals in JSON and YAML within a bounded time', () => {
    const value = { ...doc(), amount: 0.1 };
    const literal = `0.1${'0'.repeat(128_000)}1`;
    const json = JSON.stringify(value).replace('\"amount\":0.1', `\"amount\":${literal}`);
    const yaml = projectWorkflowSnapshot('flow', value).yaml.replace('amount: 0.1', `amount: ${literal}`);
    const started = performance.now();
    expect(() => projectWorkflowSnapshotJson('flow', json)).toThrow();
    expect(() => compareWorkflowSnapshotYaml('flow', yaml, value)).toThrow();
    expect(performance.now() - started).toBeLessThan(3000);
  });

  it('pins the v1 encoding and digest to a stable golden vector', () => {
    const result = projectWorkflowSnapshot('flow', { nodes: [{ id: 'a', executor: { type: 'agent' } }], id: 'flow' });
    expect(result.specJson).toBe('{"id":"flow","nodes":[{"executor":{"type":"agent"},"id":"a"}]}');
    expect(result.yaml).toBe('id: flow\nnodes:\n  - executor:\n      type: agent\n    id: a\n');
    expect(result.digest).toBe('5734a39639583f2ba33e2614b93ad406a55b95f3481399751f92898e88d202e3');
  });

  it('compares YAML independently of map ordering, quoting and comments', () => {
    const value = { id: 'flow', nodes: [{ id: 'a', executor: { type: 'agent' } }] };
    const yaml = '# header\nnodes: [{id: a, executor: {type: "agent"}}]\nid: "flow"\n';
    expect(compareWorkflowSnapshotYaml('flow', yaml, value).matches).toBe(true);
  });

  it('treats array ordering and user content as meaningful', () => {
    const value = doc();
    const original = projectWorkflowSnapshot('flow', value);
    const changed = doc(); changed.nodes[0].extra!.reverse();
    expect(projectWorkflowSnapshot('flow', changed).digest).not.toBe(original.digest);
    changed.nodes[0].extra!.reverse(); changed.content = 'different';
    expect(projectWorkflowSnapshot('flow', changed).digest).not.toBe(original.digest);
  });

  it('rejects negative zero rather than silently projecting it as positive zero', () => {
    expect(Object.is(-0, 0)).toBe(false);
    expect(() => projectWorkflowSnapshot('flow', { ...doc(), customZero: -0 })).toThrow();
    const value = { ...doc(), customZero: 0 };
    const yaml = projectWorkflowSnapshot('flow', value).yaml;
    for (const spelling of ['-0', '-0.0', '-0e10']) {
      expect(() => compareWorkflowSnapshotYaml('flow', yaml.replace('customZero: 0', `customZero: ${spelling}`), value)).toThrow();
    }
  });

  it('compares validated Git YAML with the exact JSON semantics of the DB document', () => {
    const value = doc();
    const projected = projectWorkflowSnapshot('flow', value);
    expect(compareWorkflowSnapshotYaml('flow', projected.yaml, value)).toEqual({ matches: true, gitDigest: projected.digest, dbDigest: projected.digest, version: WORKFLOW_SNAPSHOT_PROJECTION_VERSION });
    expect(compareWorkflowSnapshotYaml('flow', projected.yaml, { ...value, content: 'other' }).matches).toBe(false);
    expect(() => compareWorkflowSnapshotYaml('flow', projected.yaml.replace('id: flow', 'id: other'), value)).toThrow();
  });

  it.each([
    ['wrong identity', 'other', doc()],
    ['trimmed identity', 'flow', { ...doc(), id: ' flow ' }],
    ['missing nodes', 'flow', { id: 'flow' }],
    ['invalid semantics', 'flow', { ...doc(), nodes: [{ id: 'a', executor: { type: 'agent' }, dependsOn: ['missing'] }] }],
    ['duplicate nodes', 'flow', { ...doc(), nodes: [{ id: 'a', executor: { type: 'agent' } }, { id: 'a', executor: { type: 'agent' } }] }],
    ['undefined', 'flow', { ...doc(), extra: undefined }],
    ['undefined in array', 'flow', { ...doc(), extra: [undefined] }],
    ['NaN', 'flow', { ...doc(), extra: NaN }],
    ['infinity', 'flow', { ...doc(), extra: Infinity }],
    ['bigint', 'flow', { ...doc(), extra: 1n }],
    ['date', 'flow', { ...doc(), extra: new Date() }],
    ['function', 'flow', { ...doc(), extra: () => 1 }],
    ['invalid unicode', 'flow', { ...doc(), extra: '\ud800' }],
    ['oversize', 'flow', { ...doc(), extra: 'x'.repeat(1024 * 1024 + 1) }],
  ])('rejects %s', (_label, id, value) => {
    expect(() => projectWorkflowSnapshot(id as string, value)).toThrow();
  });

  it.each([
    ['dependsOn string', { dependsOn: 'first' }],
    ['dependsOn null', { dependsOn: null }],
    ['dependsOn object', { dependsOn: {} }],
    ['dependsOn non-string element', { dependsOn: ['first', 2] }],
    ['invalid triggerRule', { triggerRule: 'not_a_rule' }],
    ['null triggerRule', { triggerRule: null }],
    ['non-string triggerRule', { triggerRule: 5 }],
  ])('rejects original node with %s before normalization can hide it', (_label, fields) => {
    const value = doc();
    const invalid = { ...value, nodes: [value.nodes[0], { ...value.nodes[1], ...fields }] };
    expect(() => projectWorkflowSnapshot('flow', invalid)).toThrow();
    const yaml = `id: flow\nnodes: [{id: first, executor: {type: agent}}, {id: second, executor: {type: agent}, ${Object.entries(fields).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(', ')}}]\n`;
    expect(() => compareWorkflowSnapshotYaml('flow', yaml, value)).toThrow();
  });

  it.each(['all_success', 'one_success', 'all_done'])('preserves valid triggerRule %s and dependsOn unchanged', rule => {
    const value = doc();
    const withRule = { ...value, nodes: [value.nodes[0], { ...value.nodes[1], triggerRule: rule }] };
    const projection = projectWorkflowSnapshot('flow', withRule);
    expect(JSON.parse(projection.specJson)).toEqual(withRule);
    expect(parse(projection.yaml)).toEqual(withRule);
  });

  it.each([
    ['string dependsOn', { dependsOn: 'parent' }],
    ['mixed dependsOn', { dependsOn: ['parent', 1] }],
    ['unknown triggerRule', { triggerRule: 'bogus' }],
    ['null triggerRule', { triggerRule: null }],
  ])('rejects loop body %s in JSON, Git YAML, and DB comparison', (_name, fields) => {
    const child = { id: 'child', title: 'Child', phase: 'P1', executor: { type: 'agent' }, ...fields };
    const invalid = { id: 'flow', nodes: [{ id: 'loop', executor: { type: 'loop-group', maxIterations: 2, body: [child] } }] };
    const valid = { id: 'flow', nodes: [{ id: 'loop', executor: { type: 'loop-group', maxIterations: 2, body: [{ id: 'child', title: 'Child', phase: 'P1', executor: { type: 'agent' } }] } }] };
    expect(() => projectWorkflowSnapshot('flow', invalid)).toThrow();
    expect(() => compareWorkflowSnapshotYaml('flow', stringify(invalid), valid)).toThrow();
    expect(() => compareWorkflowSnapshotYaml('flow', stringify(valid), invalid)).toThrow();
  });

  it('preserves valid loop bodies and validates legacy top-level loop form', () => {
    const body = [
      { id: 'a', title: 'A', phase: 'P1', executor: { type: 'agent' }, custom: { content: 'kept' } },
      { id: 'b', title: 'B', phase: 'P1', executor: { type: 'agent' }, dependsOn: ['a'], triggerRule: 'all_done' },
    ];
    const value = { id: 'flow', nodes: [{ id: 'loop', executor: { type: 'loop-group', maxIterations: 2, body } }] };
    const projection = projectWorkflowSnapshot('flow', value);
    expect(JSON.parse(projection.specJson)).toEqual(value);
    expect(parse(projection.yaml)).toEqual(value);
    expect(compareWorkflowSnapshotYaml('flow', projection.yaml, value).matches).toBe(true);
    const legacy = { id: 'flow', nodes: [{ id: 'loop', type: 'loop-group', executor: { type: 'agent' }, body: [{ ...body[0], triggerRule: 'bad' }] }] };
    expect(() => projectWorkflowSnapshot('flow', legacy)).toThrow();
    expect(() => compareWorkflowSnapshotYaml('flow', stringify(legacy), value)).toThrow();
  });

  it('validates reusable node template bodies as well as loop bodies', () => {
    const child = { id: 'child', title: 'Child', phase: 'P1', executor: { type: 'agent' } };
    const value = { id: 'flow', nodes: [{ id: 'root', executor: { type: 'agent' } }], nodeTemplates: { t: { body: [child] } } };
    const projection = projectWorkflowSnapshot('flow', value);
    expect(compareWorkflowSnapshotYaml('flow', projection.yaml, value).matches).toBe(true);
    const invalid = { ...value, nodeTemplates: { t: { body: [{ ...child, dependsOn: 'wrong' }] } } };
    expect(() => projectWorkflowSnapshot('flow', invalid)).toThrow();
    expect(() => compareWorkflowSnapshotYaml('flow', stringify(invalid), value)).toThrow();
  });

  it('checks deeply nested loop bodies, including inside templates', () => {
    const bad = { id: 'child', executor: { type: 'agent' }, triggerRule: 'bogus' };
    const nested = { id: 'outer', executor: { type: 'loop-group', body: [{ id: 'inner', executor: { type: 'loop-group', body: [bad] } }] } };
    const value = { id: 'flow', nodes: [nested] };
    const valid = { id: 'flow', nodes: [{ id: 'root', executor: { type: 'agent' } }] };
    expect(() => projectWorkflowSnapshot('flow', value)).toThrow();
    expect(() => compareWorkflowSnapshotYaml('flow', stringify(value), valid)).toThrow();
    const template = { ...valid, nodeTemplates: { t: { body: [nested] } } };
    expect(() => projectWorkflowSnapshot('flow', template)).toThrow();
    expect(() => compareWorkflowSnapshotYaml('flow', stringify(template), valid)).toThrow();
  });

  it('fails closed on malformed loop or template body structures', () => {
    const root = { id: 'root', executor: { type: 'agent' } };
    for (const body of [undefined, null, {}, [], [null], ['node']]) {
      const loop = { id: 'flow', nodes: [{ id: 'loop', executor: { type: 'loop-group', ...(body === undefined ? {} : { body }) } }] };
      expect(() => projectWorkflowSnapshot('flow', loop)).toThrow();
      const templates = { id: 'flow', nodes: [root], nodeTemplates: { x: { ...(body === undefined ? {} : { body }) } } };
      expect(() => projectWorkflowSnapshot('flow', templates)).toThrow();
    }
  });

  it('bounds total workflow and nested body nodes and nested body depth', () => {
    const child = (i: number) => ({ id: `child${i}`, executor: { type: 'agent' } });
    const makeWide = (count: number) => ({ id: 'flow', nodes: [{ id: 'loop', executor: { type: 'loop-group', body: Array.from({ length: count }, (_, i) => child(i)) } }] });
    expect(() => projectWorkflowSnapshot('flow', makeWide(511))).not.toThrow();
    expect(() => projectWorkflowSnapshot('flow', makeWide(512))).toThrow(/node limit/i);
    const template = { id: 'flow', nodes: [child(0)], nodeTemplates: { x: { body: Array.from({ length: 512 }, (_, i) => child(i)) } } };
    expect(() => projectWorkflowSnapshot('flow', template)).toThrow(/node limit/i);
    const nested = (depth: number) => {
      let node: Record<string, unknown> = child(0);
      for (let i = 0; i < depth; i++) node = { id: `loop${i}`, executor: { type: 'loop-group', body: [node] } };
      return { id: 'flow', nodes: [node] };
    };
    expect(() => projectWorkflowSnapshot('flow', nested(8))).not.toThrow();
    expect(() => projectWorkflowSnapshot('flow', nested(9))).toThrow(/depth limit/i);
    expect(() => compareWorkflowSnapshotYaml('flow', stringify(makeWide(512)), makeWide(1))).toThrow(/node limit/i);
  });

  it('enforces the snapshot node limit before recursive validation on reverse long chains', () => {
    const makeChain = (count: number) => ({ id: 'flow', nodes: Array.from({ length: count }, (_, i) => ({
      id: `n${i}`, executor: { type: 'agent' }, ...(i + 1 < count ? { dependsOn: [`n${i + 1}`] } : {}),
    })) });
    const limit = makeChain(512);
    expect(JSON.parse(projectWorkflowSnapshot('flow', limit).specJson)).toEqual(limit);
    expect(() => projectWorkflowSnapshot('flow', makeChain(513))).toThrow(/node limit/i);
    expect(() => projectWorkflowSnapshot('flow', makeChain(5000))).toThrow(/node limit/i);
    const yaml = stringify(makeChain(513));
    expect(() => compareWorkflowSnapshotYaml('flow', yaml, limit)).toThrow(/node limit/i);
  });

  it('accepts exact YAML exponents with leading zeros and zero mantissas without losing precision', () => {
    const cases: Array<[string, number]> = [
      ['1e+0000000000000000', 1], ['1e-0000000000000001', 0.1],
      ['0e999999999999999999999999', 0], ['0e-0000000000000000001', 0],
      [`1e+${'0'.repeat(20_000)}`, 1], [`0e-${'9'.repeat(20_000)}`, 0],
    ];
    for (const [token, amount] of cases) {
      const value = { ...doc(), amount };
      const yaml = projectWorkflowSnapshot('flow', value).yaml.replace(`amount: ${amount}`, `amount: ${token}`);
      expect(compareWorkflowSnapshotYaml('flow', yaml, value).matches).toBe(true);
    }
    const value = { ...doc(), amount: 0 };
    const yaml = projectWorkflowSnapshot('flow', value).yaml.replace('amount: 0', 'amount: 1e-0000000000000999');
    expect(() => compareWorkflowSnapshotYaml('flow', yaml, value)).toThrow();
  });

  it('rejects unsafe integers rather than silently rounding YAML numbers', () => {
    expect(() => projectWorkflowSnapshot('flow', { ...doc(), big: 9007199254740992 })).toThrow();
    const yaml = projectWorkflowSnapshot('flow', doc()).yaml + 'big: 9007199254740993\n';
    expect(() => compareWorkflowSnapshotYaml('flow', yaml, doc())).toThrow();
  });

  it('rejects lossy YAML decimal conversion but accepts equivalent decimal spellings', () => {
    const value = { ...doc(), amount: 0.1 };
    const base = projectWorkflowSnapshot('flow', value).yaml;
    expect(compareWorkflowSnapshotYaml('flow', base.replace('amount: 0.1', 'amount: 0.10'), value).matches).toBe(true);
    expect(() => compareWorkflowSnapshotYaml('flow', base.replace('amount: 0.1', 'amount: 0.10000000000000001'), value)).toThrow();
    expect(() => compareWorkflowSnapshotYaml('flow', base.replace('amount: 0.1', 'amount: 1e-999'), value)).toThrow();
  });

  it('accepts lossless standard YAML hexadecimal and octal integers', () => {
    const value = { ...doc(), hex: 16, octal: 16, max: Number.MAX_SAFE_INTEGER, zero: 0, padded: 16 };
    const base = projectWorkflowSnapshot('flow', value).yaml;
    const yaml = base.replace('hex: 16', 'hex: 0x10').replace('octal: 16', 'octal: 0o20')
      .replace(`max: ${Number.MAX_SAFE_INTEGER}`, 'max: 0x1fffffffffffff')
      .replace('zero: 0', 'zero: 0o0').replace('padded: 16', 'padded: 0x00010');
    expect(compareWorkflowSnapshotYaml('flow', yaml, value).matches).toBe(true);
    expect(compareWorkflowSnapshotYaml('flow', yaml.replace('max: 0x1fffffffffffff', 'max: 0o377777777777777777'), value).matches).toBe(true);
    for (const token of ['0x20000000000001', '0o400000000000000001']) {
      expect(() => compareWorkflowSnapshotYaml('flow', base.replace('hex: 16', `hex: ${token}`), value)).toThrow();
    }
  });

  it('rejects decoded duplicate string keys in block, flow and nested maps', () => {
    const base = projectWorkflowSnapshot('flow', doc()).yaml;
    for (const suffix of [
      'extra: {a: 1, "a": 2}\n',
      'extra:\n  "\\u0061": 1\n  a: 2\n',
      'extra: {inner: {a: 1, a: 2}}\n',
      'id: flow\n', 'extra: {__proto__: 1, "__proto__": 2}\n',
    ]) expect(() => compareWorkflowSnapshotYaml('flow', base + suffix, doc())).toThrow();
  });

  it('handles a 40,000-key valid YAML mapping within a bounded time', () => {
    const entries = Array.from({ length: 40_000 }, (_, i) => `  k${i}: 0\n`).join('');
    const yaml = 'id: flow\nnodes: [{id: a, executor: {type: agent}}]\nextra:\n' + entries;
    expect(Buffer.byteLength(yaml)).toBeLessThan(1024 * 1024);
    const started = performance.now();
    const result = compareWorkflowSnapshotYaml('flow', yaml, { id: 'flow', nodes: [{ id: 'a', executor: { type: 'agent' } }] });
    expect(result.matches).toBe(false);
    expect(performance.now() - started).toBeLessThan(6000);
  }, 25_000);

  it('keeps duplicate checks local to each map and catches a duplicate at the end of a large map', () => {
    const value = { ...doc(), extra: [{ a: 1 }, { a: 2 }] };
    const base = projectWorkflowSnapshot('flow', value).yaml;
    expect(compareWorkflowSnapshotYaml('flow', base, value).matches).toBe(true);
    const entries = Array.from({ length: 40_000 }, (_, i) => `  k${i}: 0\n`).join('');
    const yaml = 'id: flow\nnodes: [{id: a, executor: {type: agent}}]\nextra:\n' + entries + '  "k0": 1\n';
    const started = performance.now();
    expect(() => compareWorkflowSnapshotYaml('flow', yaml, value)).toThrow();
    expect(performance.now() - started).toBeLessThan(6000);
  }, 25_000);

  it('preserves tricky JSON keys and strings through YAML round trips', () => {
    const value = JSON.parse('{"id":"flow","nodes":[{"id":"a","executor":{"type":"agent"}}],"__proto__":{"polluted":true},"10":"ten","2":"two","special":"yes: # no\\n\\u0000"}');
    const result = projectWorkflowSnapshot('flow', value);
    expect(JSON.parse(result.specJson)).toEqual(value);
    expect(compareWorkflowSnapshotYaml('flow', result.yaml, value).matches).toBe(true);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it('rejects unsupported YAML constructs even when the workflow itself is valid', () => {
    const base = projectWorkflowSnapshot('flow', doc()).yaml;
    for (const suffix of [
      'duplicate: a\nduplicate: b\n', 'extra: .nan\n',
      'a: &a [1]\nb: *a\n', '? [complex, key]\n: value\n',
      'x: !custom value\n', '---\nid: other\n',
      '123: numeric-key\n',
    ]) expect(() => compareWorkflowSnapshotYaml('flow', base + suffix, doc())).toThrow();
  });

  it('rejects cycles, sparse arrays, getters, hidden data, symbol keys, and excessive nesting', () => {
    const cycle: Record<string, unknown> = doc(); cycle.self = cycle;
    const sparse = Array(1);
    const getter = Object.defineProperty(doc(), 'surprise', { enumerable: true, get() { throw new Error('getter ran'); } });
    const hidden = Object.defineProperty(doc(), 'hidden', { enumerable: false, value: 1 });
    const symbol = { ...doc(), [Symbol('secret')]: 1 };
    let nested: unknown = 1; for (let i = 0; i < 150; i++) nested = { nested };
    for (const value of [cycle, { ...doc(), sparse }, getter, hidden, symbol, { ...doc(), nested }]) {
      expect(() => projectWorkflowSnapshot('flow', value)).toThrow();
    }
  });

  it.each([
    'id: flow\nid: flow\nnodes: []\n',
    'id: flow\nnodes: [',
    'id: flow\nnodes: []\nextra: .nan\n',
    'id: flow\nnodes: []\n---\nid: other\n',
    'id: flow\nnodes: []\na: &a [1]\nb: *a\n',
    'id: flow\nnodes: []\n? [complex, key]\n: value\n',
    'id: flow\nnodes: []\nx: !custom value\n',
    'x'.repeat(1024 * 1024 + 1),
  ])('fails closed on invalid or unsafe Git YAML', yaml => {
    expect(() => compareWorkflowSnapshotYaml('flow', yaml, doc())).toThrow();
  });
});
