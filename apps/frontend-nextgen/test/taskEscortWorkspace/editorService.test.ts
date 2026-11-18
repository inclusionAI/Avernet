import {
  editorPalette,
  initialEditorNodes,
  serializeEditorWorkflow,
  validateEditorNodes,
} from '@/services/taskEscortWorkspace/editorModel';
import { createEditorSession } from '@/services/taskEscortWorkspace/editorSession';
import { load } from 'js-yaml';

describe('TaskEscort editor model', () => {
  it('has plain-data nodes and correct palette mappings', () => {
    expect(initialEditorNodes).toHaveLength(5);
    expect(JSON.parse(JSON.stringify(initialEditorNodes))).toEqual(initialEditorNodes);
    expect(editorPalette.map(({ nodeId }) => nodeId)).toEqual(['start', 'analyze', 'fetch', null, 'guard']);
    for (const item of editorPalette.filter((entry) => entry.nodeId !== null)) {
      expect(initialEditorNodes.find((node) => node.id === item.nodeId)?.kind).toBe(item.kind);
    }
  });
  it('validates the complete DAG and rejects missing/duplicate connections or guardian', () => {
    expect(validateEditorNodes(initialEditorNodes).valid).toBe(true);
    expect(validateEditorNodes([]).valid).toBe(false);
    expect(validateEditorNodes(initialEditorNodes.slice(1)).valid).toBe(false);
    expect(validateEditorNodes([...initialEditorNodes, initialEditorNodes[0]]).valid).toBe(false);
    expect(
      validateEditorNodes(initialEditorNodes.map((n) => (n.id === 'guard' ? { ...n, kind: 'tool' as const } : n)))
        .valid,
    ).toBe(false);
  });
  it.each(['', ' ', '0', '-1', '3601', '1.2', 'Infinity', 'NaN', '2e2'])('rejects invalid timeout %p', (timeout) => {
    expect(validateEditorNodes(initialEditorNodes.map((n, i) => (i === 1 ? { ...n, timeout } : n))).valid).toBe(false);
  });
  it.each(['', '-1', '11', '1.2', 'Infinity', '2e0'])('rejects invalid retries %p', (retries) => {
    expect(validateEditorNodes(initialEditorNodes.map((n, i) => (i === 1 ? { ...n, retries } : n))).valid).toBe(false);
  });
  it.each(['1', '3600'])('accepts timeout boundary %s', (timeout) => {
    expect(validateEditorNodes(initialEditorNodes.map((n) => ({ ...n, timeout }))).valid).toBe(true);
  });
  it.each(['0', '10'])('accepts retry boundary %s', (retries) => {
    expect(validateEditorNodes(initialEditorNodes.map((n) => ({ ...n, retries }))).valid).toBe(true);
  });
  it('requires trimmed names and serializes YAML safely with descriptions and dependencies', () => {
    expect(validateEditorNodes(initialEditorNodes.map((n) => ({ ...n, name: ' ' }))).valid).toBe(false);
    const yaml = serializeEditorWorkflow(
      initialEditorNodes.map((n) => ({ ...n, name: 'a: "b"\nc' })),
      { id: 'first', name: 'Flow: name' },
      'v2.4',
    );
    expect(yaml).toContain('title: "Flow: name"');
    expect(yaml).toContain('title: "a: \\"b\\"\\nc"');
    expect(load(yaml)).toEqual(
      expect.objectContaining({
        nodes: expect.arrayContaining([expect.objectContaining({ id: 'guard', depends_on: ['fetch', 'analyze'] })]),
      }),
    );
    expect(yaml).toContain('description:');
  });
});

describe('TaskEscort editor scoped session', () => {
  it('keeps saved workflows isolated and never shares state between workspace instances', () => {
    const first = createEditorSession('demo');
    first.patch('one', { name: 'one saved' });
    expect(first.save('one').ok).toBe(true);
    expect(first.getSnapshot('two').nodes[1].name).toBe('数据采集');
    first.patch('two', { name: 'two saved' });
    first.save('two');
    expect(first.getSnapshot('one').nodes[1].name).toBe('one saved');
    expect(createEditorSession('demo').getSnapshot('one').nodes[1].name).toBe('数据采集');
  });
  it('supports undo/redo, skips unchanged patches, and clears redo after divergent edits', () => {
    const service = createEditorSession('demo');
    const initial = service.getSnapshot('one');
    service.patch('one', { name: '数据采集' });
    expect(service.getSnapshot('one')).toBe(initial);
    service.patch('one', { name: 'changed' });
    expect(service.getSnapshot('one').dirty).toBe(true);
    service.undo('one');
    expect(service.getSnapshot('one').nodes[1].name).toBe('数据采集');
    expect(service.getSnapshot('one').dirty).toBe(false);
    service.redo('one');
    expect(service.getSnapshot('one').nodes[1].name).toBe('changed');
    service.undo('one');
    service.patch('one', { name: 'another' });
    expect(service.getSnapshot('one').future).toHaveLength(0);
  });
  it('rejects invalid saves and simulated publishes without clearing dirty state', () => {
    const service = createEditorSession('demo');
    service.patch('one', { name: ' ', timeout: '0', retries: '11' });
    expect(service.save('one').ok).toBe(false);
    expect(service.publish('one').ok).toBe(false);
    expect(service.getSnapshot('one').dirty).toBe(true);
    expect(service.getSnapshot('one').version).toBe('v2.4');
    service.restore('one');
    expect(service.getSnapshot('one').nodes[1].name).toBe('数据采集');
    expect(service.getSnapshot('one').dirty).toBe(false);
  });
  it('simulated publishes update only this instance and reset clears all workflows', () => {
    const service = createEditorSession('demo');
    service.patch('one', { name: 'saved' });
    expect(service.publish('one')).toEqual(expect.objectContaining({ ok: true }));
    expect(service.getSnapshot('one').version).toBe('v2.5');
    expect(service.getSnapshot('one').dirty).toBe(false);
    service.patch('two', { name: 'another' });
    service.save('two');
    service.reset();
    expect(service.getSnapshot('one').version).toBe('v2.4');
    expect(service.getSnapshot('two').nodes[1].name).toBe('数据采集');
  });
  it('selects known nodes only, emits mutations and unsubscribes', () => {
    const service = createEditorSession('demo');
    const listener = jest.fn();
    const unsubscribe = service.subscribe(listener);
    service.select('one', 'analyze');
    expect(service.getSnapshot('one').selected).toBe('analyze');
    expect(service.getSnapshot('one').dirty).toBe(false);
    service.select('one', 'missing');
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    service.patch('one', { name: 'new' });
    expect(listener).toHaveBeenCalledTimes(1);
  });
  it('never exposes fake nodes or permits writes in live mode', () => {
    const service = createEditorSession('live');
    expect(service.getSnapshot('one').nodes).toEqual([]);
    service.patch('one', { name: 'forbidden' });
    service.undo('one');
    service.redo('one');
    expect(service.save('one').ok).toBe(false);
    expect(service.publish('one').ok).toBe(false);
    expect(service.getSnapshot('one').nodes).toEqual([]);
  });
});
