/** @jest-environment jsdom */
import type { EditorNode } from '@/domain/taskEscortParity/editorTypes';
import { EditorInspector, EditorSettings } from '@/pages/TaskEscort/parity/editor/EditorInspector';
import { parseInspectorJson, validateNode } from '@/pages/TaskEscort/parity/editor/inspector/validation';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
const node: EditorNode = {
  id: 'a',
  title: '原名称',
  executor: { type: 'cli-script', command: 'echo', args: ['a b'], timeoutMs: 123, extension: { keep: true } },
  custom: { keep: 7 },
};
it('explicitly applies semantic drafts preserving extensions', () => {
  const change = jest.fn();
  render(<EditorInspector node={node} close={jest.fn()} onChange={change} />);
  fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: '新名称' } });
  fireEvent.change(screen.getByLabelText('命令或脚本路径'), { target: { value: 'printf' } });
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('应用节点配置'));
  expect(change).toHaveBeenCalledWith({ ...node, title: '新名称', executor: { ...node.executor, command: 'printf' } });
  expect(node.title).toBe('原名称');
});
it('defaults readonly', () => {
  render(<EditorInspector node={node} close={jest.fn()} />);
  expect(screen.getByLabelText('节点名称')).toHaveAttribute('readonly');
  expect(screen.getByText('应用节点配置')).toBeDisabled();
});
it('rejects protected ids and omitted extension keys', () => {
  expect(() => validateNode(node, { ...node, id: 'other' })).toThrow('只读');
  expect(() => validateNode(node, { ...node, custom: {} })).toThrow('不能静默删除');
});
it.each(['{"n":9007199254740993}', '{"n":0.10000000000000001}', '{"n":1e999}', '{"n":-0}', 'n: 2'])(
  'rejects unsafe JSON %s',
  (text) => {
    expect(() => parseInspectorJson(text)).toThrow();
  },
);
it('validates dependencies and referenced branches', () => {
  const a = { ...node, onResult: { branches: [{ branchId: 'yes', match: {} }] } };
  const b: EditorNode = { id: 'b', executor: { type: 'done' }, dependsOn: ['a'], branchId: 'yes' };
  expect(() => validateNode(a, { ...a, dependsOn: ['b'] }, [a, b])).toThrow('环');
  expect(() => validateNode(a, { ...a, onResult: { branches: [] } }, [a, b])).toThrow('已被引用');
});
it('settings preserve nodes and unknown settings', () => {
  const spec = { id: 'workflow', title: '流程', nodes: [node], custom: { keep: true } };
  const change = jest.fn();
  render(<EditorSettings spec={spec} open onOpenChange={jest.fn()} onChange={change} />);
  fireEvent.change(screen.getByLabelText('流程名称'), { target: { value: '新流程' } });
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('应用流程设置'));
  expect(change).toHaveBeenCalledWith({ ...spec, title: '新流程' });
});
it('keeps invalid raw draft visible and reports errors without invoking change', () => {
  const change = jest.fn();
  render(<EditorInspector node={node} close={jest.fn()} onChange={change} />);
  fireEvent.change(screen.getByLabelText('完整节点 JSON'), { target: { value: '{broken' } });
  fireEvent.click(screen.getByText('应用节点配置'));
  expect(screen.getByRole('alert')).toBeInTheDocument();
  expect(screen.getByLabelText('完整节点 JSON')).toHaveValue('{broken');
  expect(change).not.toHaveBeenCalled();
});
it('rejects dangerous object keys and duplicate keys but preserves typed JSON', () => {
  expect(() => parseInspectorJson('{"__proto__":{"polluted":true}}')).toThrow('不安全');
  expect(() => parseInspectorJson('{"a":1,"a":2}')).toThrow();
  expect(parseInspectorJson('{"n":1e3,"b":true,"s":"true","v":null}')).toEqual({
    n: 1000,
    b: true,
    s: 'true',
    v: null,
  });
});
it('rejects a fallback branch before a scalar branch', () => {
  const after = {
    ...node,
    onResult: {
      branches: [
        { branchId: 'fallback', match: {} },
        { branchId: 'ok', match: { ok: true } },
      ],
    },
  };
  expect(() => validateNode(node, after, [node])).toThrow('最后');
});
it('supports explicit argument JSON draft before node apply', () => {
  const change = jest.fn();
  render(<EditorInspector node={node} close={jest.fn()} onChange={change} />);
  fireEvent.change(screen.getByLabelText('参数 JSON'), { target: { value: '["space preserved", "--flag"]' } });
  fireEvent.click(screen.getByText('更新参数草稿'));
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('应用节点配置'));
  expect(change).toHaveBeenCalledWith({ ...node, executor: { ...node.executor, args: ['space preserved', '--flag'] } });
});
it.each(['discard', 'apply'])('reports node pending lifecycle on %s', (action) => {
  const pending = jest.fn();
  const view = render(<EditorInspector node={node} close={jest.fn()} onChange={jest.fn()} onPendingChange={pending} />);
  expect(pending).toHaveBeenLastCalledWith(false);
  fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: '修改' } });
  expect(pending).toHaveBeenLastCalledWith(true);
  fireEvent.click(screen.getByText(action === 'apply' ? '应用节点配置' : '放弃草稿'));
  expect(pending).toHaveBeenLastCalledWith(false);
  fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: '未应用' } });
  view.unmount();
  expect(pending).toHaveBeenLastCalledWith(false);
});
it('guards settings close until discard or apply and clears pending on unmount', () => {
  const pending = jest.fn();
  const close = jest.fn();
  const spec = { id: 'workflow', title: '流程', nodes: [node] };
  const view = render(
    <EditorSettings spec={spec} open onOpenChange={close} onChange={jest.fn()} onPendingChange={pending} />,
  );
  fireEvent.change(screen.getByLabelText('流程名称'), { target: { value: '修改' } });
  expect(pending).toHaveBeenLastCalledWith(true);
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  expect(close).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('放弃设置草稿'));
  expect(pending).toHaveBeenLastCalledWith(false);
  fireEvent.change(screen.getByLabelText('流程描述'), { target: { value: '描述' } });
  fireEvent.click(screen.getByText('应用流程设置'));
  expect(pending).toHaveBeenLastCalledWith(false);
  fireEvent.change(screen.getByLabelText('流程名称'), { target: { value: '未应用' } });
  view.unmount();
  expect(pending).toHaveBeenLastCalledWith(false);
});
it('renders duplicate and delete callback errors', async () => {
  const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
  render(
    <EditorInspector
      node={node}
      close={jest.fn()}
      onChange={jest.fn()}
      onDuplicate={() => {
        throw new Error('复制失败');
      }}
      onDelete={() => Promise.reject(new Error('删除失败'))}
    />,
  );
  fireEvent.click(screen.getByText('复制节点'));
  expect(await screen.findByRole('alert')).toHaveTextContent('复制失败');
  fireEvent.click(screen.getByText('删除节点'));
  expect(await screen.findByText('删除失败')).toBeInTheDocument();
  confirm.mockRestore();
});
it.each(['["new argument"]', '{invalid'])('protects unapplied argument text %s until explicit discard', (text) => {
  const pending = jest.fn();
  const close = jest.fn();
  const change = jest.fn();
  render(
    <EditorInspector
      node={node}
      close={close}
      onChange={change}
      onPendingChange={pending}
      onDuplicate={jest.fn()}
      onDelete={jest.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText('参数 JSON'), { target: { value: text } });
  expect(pending).toHaveBeenLastCalledWith(true);
  expect(screen.getByText('关闭节点面板')).toBeDisabled();
  expect(screen.getByText('复制节点')).toBeDisabled();
  expect(screen.getByText('删除节点')).toBeDisabled();
  expect(screen.getByText('应用节点配置')).toBeDisabled();
  fireEvent.click(screen.getByText('关闭节点面板'));
  expect(close).not.toHaveBeenCalled();
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('放弃参数草稿'));
  expect(screen.getByLabelText('参数 JSON')).toHaveValue(JSON.stringify(node.executor.args, null, 2));
  expect(pending).toHaveBeenLastCalledWith(false);
});
it('keeps rejected invalid argument JSON pending until explicit argument discard', () => {
  expect.assertions(7);
  const pending = jest.fn();
  const change = jest.fn();
  render(<EditorInspector node={node} close={jest.fn()} onChange={change} onPendingChange={pending} />);
  fireEvent.change(screen.getByLabelText('参数 JSON'), { target: { value: '{invalid' } });
  fireEvent.click(screen.getByText('更新参数草稿'));
  expect(screen.getByRole('alert')).toBeInTheDocument();
  expect(screen.getByLabelText('参数 JSON')).toHaveValue('{invalid');
  expect(pending).toHaveBeenLastCalledWith(true);
  expect(screen.getByText('应用节点配置')).toBeDisabled();
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('放弃参数草稿'));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(pending).toHaveBeenLastCalledWith(false);
});
it('whole node discard resets invalid argument text and its error', () => {
  const pending = jest.fn();
  render(<EditorInspector node={node} close={jest.fn()} onChange={jest.fn()} onPendingChange={pending} />);
  fireEvent.change(screen.getByLabelText('参数 JSON'), { target: { value: '{invalid' } });
  fireEvent.click(screen.getByText('更新参数草稿'));
  fireEvent.click(screen.getByText('放弃草稿'));
  expect(screen.getByLabelText('参数 JSON')).toHaveValue(JSON.stringify(node.executor.args, null, 2));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(pending).toHaveBeenLastCalledWith(false);
});
it('keeps argument edits pending through update until applying the node', () => {
  const pending = jest.fn();
  render(<EditorInspector node={node} close={jest.fn()} onChange={jest.fn()} onPendingChange={pending} />);
  fireEvent.change(screen.getByLabelText('参数 JSON'), { target: { value: '["new"]' } });
  fireEvent.click(screen.getByText('更新参数草稿'));
  expect(pending).toHaveBeenLastCalledWith(true);
  expect(screen.getByText('应用节点配置')).toBeEnabled();
  fireEvent.click(screen.getByText('应用节点配置'));
  expect(pending).toHaveBeenLastCalledWith(false);
});
it('rejects removing the final dependency path to an existing branch origin', () => {
  const a: EditorNode = { ...node, onResult: { branches: [{ branchId: 'yes', match: {} }] } };
  const b: EditorNode = { id: 'b', executor: { type: 'done' }, dependsOn: ['a'], branchId: 'yes' };
  expect(() => validateNode(b, { ...b, dependsOn: [] }, [a, b])).toThrow('失去最后一条有效来源路径');
});
