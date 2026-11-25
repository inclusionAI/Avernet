/** @jest-environment jsdom */
import type { EditorSpec } from '@/domain/taskEscortParity/editorTypes';
import { EditorGraph } from '@/pages/TaskEscort/parity/editor/EditorGraph';
import { computeDagLayout } from '@/pages/TaskEscort/parity/editor/canvas/graph';
import {
  connectNodes,
  deleteNode,
  duplicateNode,
  removeConnection,
} from '@/pages/TaskEscort/parity/editor/canvas/model';
import { NODE_CATALOG } from '@/pages/TaskEscort/parity/editor/canvas/nodeCatalog';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';

// Only the browser geometry/gesture surface is mocked; cards, palette, contracts
// and immutable graph mutations are real. React Flow itself is used in production.
let flow: any;
jest.mock('@xyflow/react', () => ({
  ReactFlowProvider: ({ children }: any) => children,
  ReactFlow: (props: any) => {
    flow = props;
    const Card = props.nodeTypes.editorNode;
    return (
      <div data-testid="flow">
        {props.nodes.map((node: any) => (
          <div
            key={node.id}
            role="button"
            tabIndex={0}
            aria-label={node.ariaLabel}
            onClick={() => props.onNodeClick({}, node)}
          >
            <Card {...node} isConnectable={props.nodesConnectable} />
          </div>
        ))}
        {props.children}
      </div>
    );
  },
  Handle: ({ id, type, isConnectable }: any) => (
    <span data-testid={`handle-${id || type}`} data-connectable={isConnectable} />
  ),
  Position: { Top: 'top', Bottom: 'bottom', Right: 'right' },
  useUpdateNodeInternals: () => () => {},
  useReactFlow: () => ({ screenToFlowPosition: (position: any) => position, setCenter: jest.fn(), fitView: jest.fn() }),
  Background: () => <div data-testid="dots" />,
  Controls: () => <div aria-label="画布缩放控制" />,
  MiniMap: () => <div aria-label="流程小地图" />,
}));
const base: EditorSpec = {
  id: 'wf',
  title: 'Workflow',
  extension: { keep: true },
  draft: { untouched: true },
  nodes: [
    {
      id: 'a',
      title: '开始',
      phase: 'P1',
      _x: 12,
      _y: 34,
      executor: { type: 'cli-script', command: 'echo ok', unknown: 7 },
      extension: { keep: true },
    },
    { id: 'b', title: '结束', phase: 'P1', executor: { type: 'done' } },
  ],
};
function branchSpec(): EditorSpec {
  return {
    ...base,
    nodes: [
      { ...base.nodes[0], onResult: { branches: [{ branchId: 'yes', match: {}, complete: false, extra: 'keep' }] } },
      base.nodes[1],
    ],
  };
}
describe('canvas graph contracts', () => {
  it('duplicates isolated configuration using fresh identity and rejects branch origins', () => {
    // jsdom lacks structuredClone; exercise the pure model using Node's equivalent
    // for this JSON-shaped workflow boundary.
    const clone = globalThis.structuredClone;
    if (!clone) globalThis.structuredClone = (value) => JSON.parse(JSON.stringify(value));
    try {
      const first = duplicateNode(base, 'a');
      const second = duplicateNode(first, 'a');
      expect(new Set(second.nodes.map((node) => node.id)).size).toBe(4);
      expect(first.nodes[2]).toMatchObject({
        title: '开始 副本',
        _x: 12,
        _y: 34,
        executor: base.nodes[0].executor,
        extension: { keep: true },
      });
      expect(first.nodes[2].executor).not.toBe(base.nodes[0].executor);
      expect(() => duplicateNode(branchSpec(), 'a')).toThrow(/分支/);
      expect(() => duplicateNode(base, 'missing')).toThrow(/missing/);
    } finally {
      globalThis.structuredClone = clone;
    }
  });
  it('preserves unknown fields and coordinates without mutating input', () => {
    const snapshot = JSON.stringify(base);
    const next = connectNodes(base, 'a', 'b');
    expect(next.extension).toBe(base.extension);
    expect(next.nodes[0]).toBe(base.nodes[0]);
    expect(next.nodes[1].dependsOn).toEqual(['a']);
    expect(removeConnection(next, 'a', 'b').nodes[1].dependsOn).toEqual([]);
    expect(JSON.stringify(base)).toBe(snapshot);
  });
  it('rejects self edges, duplicates, cycles and missing endpoints', () => {
    expect(() => connectNodes(base, 'a', 'a')).toThrow(/self/);
    expect(() => connectNodes(base, 'missing', 'b')).toThrow(/missing/);
    const next = connectNodes(base, 'a', 'b');
    expect(() => connectNodes(next, 'a', 'b')).toThrow(/duplicate/);
    expect(() => connectNodes(next, 'b', 'a')).toThrow(/cycle/);
  });
  it('requires named branch handles and protects last branch ancestry', () => {
    const spec = branchSpec();
    expect(() => connectNodes(spec, 'a', 'b')).toThrow(/分支/);
    expect(() => connectNodes(spec, 'a', 'b', 'no')).toThrow(/分支/);
    const next = connectNodes(spec, 'a', 'b', 'yes');
    expect(next.nodes[1].branchId).toBe('yes');
    expect(next.nodes[0].onResult).toBe(spec.nodes[0].onResult);
    expect(() => removeConnection(next, 'a', 'b')).toThrow(/来源/);
    expect(() => deleteNode(next, 'a')).toThrow(/引用/);
  });
  it('cleans dependency references but refuses known runtime references', () => {
    const next = connectNodes(base, 'a', 'b');
    expect(deleteNode(next, 'a').nodes[0].dependsOn).toEqual([]);
    const referenced = {
      ...next,
      nodes: [next.nodes[0], { ...next.nodes[1], executor: { type: 'human', prompt: '{{ nodes.a.output }}' } }],
    };
    expect(() => deleteNode(referenced, 'a')).toThrow(/引用/);
  });
  it('lays dependency levels top-to-bottom and bounds cyclic imports', () => {
    const layout = computeDagLayout(connectNodes(base, 'a', 'b').nodes);
    expect(layout.get('b')!.y).toBeGreaterThan(layout.get('a')!.y);
    const cycle = [
      { ...base.nodes[0], dependsOn: ['b'] },
      { ...base.nodes[1], dependsOn: ['a'] },
    ];
    expect(computeDagLayout(cycle).size).toBe(2);
    expect(NODE_CATALOG).toHaveLength(13);
  });
});
describe('visual canvas', () => {
  it('delegates bounded history to host callbacks and preserves input-native undo', () => {
    const onUndo = jest.fn(),
      onRedo = jest.fn();
    const { rerender } = render(
      <EditorGraph
        spec={base}
        selectedId={null}
        select={jest.fn()}
        onChange={jest.fn()}
        canUndo
        canRedo
        onUndo={onUndo}
        onRedo={onRedo}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '撤销' }));
    fireEvent.click(screen.getByRole('button', { name: '重做' }));
    fireEvent.keyDown(screen.getByRole('region', { name: '工作流画布' }), { key: 'z', ctrlKey: true });
    fireEvent.keyDown(screen.getByRole('region', { name: '工作流画布' }), { key: 'z', metaKey: true, shiftKey: true });
    fireEvent.keyDown(screen.getByLabelText('查找画布节点'), { key: 'z', ctrlKey: true });
    expect(onUndo).toHaveBeenCalledTimes(2);
    expect(onRedo).toHaveBeenCalledTimes(2);
    rerender(
      <EditorGraph
        spec={base}
        selectedId={null}
        select={jest.fn()}
        onChange={jest.fn()}
        disabled
        canUndo
        canRedo
        onUndo={onUndo}
        onRedo={onRedo}
      />,
    );
    expect(screen.getByRole('button', { name: '撤销' })).toBeDisabled();
    fireEvent.keyDown(screen.getByRole('region', { name: '工作流画布' }), { key: 'z', ctrlKey: true });
    expect(onUndo).toHaveBeenCalledTimes(2);
  });
  it('connects, removes edges and deletes nodes through native flow callbacks', async () => {
    const onChange = jest.fn();
    const { rerender } = render(<EditorGraph spec={base} selectedId={null} select={jest.fn()} onChange={onChange} />);
    act(() => flow.onConnect({ source: 'a', target: 'b', sourceHandle: 'source' }));
    const connected = onChange.mock.calls[0][0];
    expect(connected.nodes[1].dependsOn).toEqual(['a']);
    rerender(<EditorGraph spec={connected} selectedId={null} select={jest.fn()} onChange={onChange} />);
    await act(async () => {
      await flow.onBeforeDelete({ nodes: [], edges: [flow.edges[0]] });
    });
    expect(onChange.mock.calls[1][0].nodes[1].dependsOn).toEqual([]);
    await act(async () => {
      await flow.onBeforeDelete({ nodes: [{ id: 'a' }], edges: flow.edges });
    });
    expect(onChange.mock.calls[2][0].nodes.map((node: any) => node.id)).toEqual(['b']);
    expect(onChange.mock.calls[2][0].extension).toEqual({ keep: true });
  });

  it('defaults read-only but leaves node selection, search and navigation available', () => {
    const select = jest.fn();
    render(<EditorGraph spec={base} selectedId="a" select={select} />);
    expect(screen.getByRole('button', { name: '添加AI 处理' })).toBeDisabled();
    expect(flow.nodesConnectable).toBe(false);
    expect(flow.deleteKeyCode).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '开始 · cli-script' }));
    expect(select).toHaveBeenCalledWith('a');
    expect(screen.getByLabelText('流程小地图')).toBeInTheDocument();
    expect(screen.getByTestId('dots')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('查找画布节点'), { target: { value: '结束' } });
    expect(screen.getByRole('button', { name: /结束 b/ })).toBeInTheDocument();
  });
  it('adds catalog nodes by click and drop with UI-only position', () => {
    const onChange = jest.fn();
    render(<EditorGraph spec={base} selectedId={null} select={jest.fn()} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: '添加AI 处理' }));
    expect(onChange.mock.calls[0][0].nodes[2]).toMatchObject({ executor: { type: 'embedded-agent', prompt: '' } });
    expect(onChange.mock.calls[0][0].nodes[2]).not.toHaveProperty('_x');
    fireEvent.drop(screen.getByTestId('editor-canvas-dropzone'), {
      clientX: 30,
      clientY: 60,
      dataTransfer: { getData: () => 'done' },
    });
    expect(onChange.mock.calls[1][0].nodes[2].executor.type).toBe('done');
    expect(onChange.mock.calls[1][0].draft).toBe(base.draft);
  });
  it('gates mutations even when readonly handlers are invoked directly', async () => {
    const onChange = jest.fn();
    render(<EditorGraph spec={base} selectedId="a" select={jest.fn()} onChange={onChange} disabled />);
    await act(async () => {
      flow.onConnect({ source: 'a', target: 'b', sourceHandle: 'source' });
      await flow.onBeforeDelete({ nodes: [{ id: 'a' }], edges: [] });
    });
    expect(onChange).not.toHaveBeenCalled();
  });
  it('draws labeled branch handles and preserves imported positions on UI drag', () => {
    const spec = connectNodes(branchSpec(), 'a', 'b', 'yes');
    const onChange = jest.fn();
    render(<EditorGraph spec={spec} selectedId={null} select={jest.fn()} onChange={onChange} />);
    expect(flow.edges[0]).toMatchObject({ label: 'yes', sourceHandle: 'branch:yes' });
    expect(screen.getByTestId('handle-branch:yes')).toBeInTheDocument();
    expect(flow.nodes[0].position).toEqual({ x: 12, y: 34 });
    act(() => flow.onNodesChange([{ type: 'position', id: 'a', position: { x: 90, y: 110 } }]));
    expect(flow.nodes[0].position).toEqual({ x: 90, y: 110 });
    fireEvent.click(screen.getByRole('button', { name: '总览' }));
    expect(onChange).not.toHaveBeenCalled();
    expect(spec.nodes[0]._x).toBe(12);
  });
});
