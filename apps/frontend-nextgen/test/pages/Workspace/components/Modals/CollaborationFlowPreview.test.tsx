/** @jest-environment jsdom */
import { CollaborationFlowPreview } from '@/pages/Workspace/components/Modals/CollaborationFlowPreview';
import type { CollaborationDefinitionGraphPreview } from '@/domain/collaboration/graphTypes';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { fixture, nestedFixture } from './collaborationLoopFixtures';

// ReactFlow 的几何测量依赖浏览器布局；保留生产节点/连线组件来验证交给画布的逻辑结构。
jest.mock('@xyflow/react', () => ({
  Position: { Top: 'top', Bottom: 'bottom', Right: 'right' },
  Handle: () => null,
  Background: () => null,
  Controls: () => null,
  getBezierPath: () => ['M 0 0 L 1 1', 0, 0],
  BaseEdge: ({ label }: { label: string }) => <text>{label}</text>,
  ReactFlow: ({ nodes, edges, nodeTypes, edgeTypes }: any) => (
    <div>
      {nodes.map((node: any) => {
        const Component = nodeTypes[node.type];
        return <div key={node.id} data-testid={node.id} data-parent={node.parentId}><Component data={node.data} /></div>;
      })}
      <svg>{edges.map((edge: any) => {
        const Component = edgeTypes[edge.type];
        return <Component key={edge.id} {...edge} sourceX={0} sourceY={0} targetX={100} targetY={100} />;
      })}</svg>
    </div>
  ),
}));

it('renders one logical body, loop limits and distinct exit labels without mutating the graph', () => {
  const graph = fixture();
  graph.loops!.revision_rounds.continue_display_name = '根据意见修订';
  const before = JSON.stringify(graph);
  const { container } = render(<CollaborationFlowPreview graph={graph} initialNodes={['draft-1']} bindingViews={{}} />);
  expect(screen.getByRole('region', { name: '协作流程预览' })).toBeInTheDocument();
  expect(container.querySelectorAll('[data-loop-container]')).toHaveLength(1);
  expect(container.querySelectorAll('[data-loop-id]')).toHaveLength(2);
  expect(screen.getByText('根据意见修订')).toBeInTheDocument();
  expect(container.querySelector('[data-loop-route="exhausted"]')).toHaveAttribute('data-actual-outcome', 'revise');
  expect(container.querySelector('[data-loop-route="break"]')).toHaveAttribute('data-actual-outcome', 'approved');
  expect(screen.getByText('循环限制').closest('details')).not.toHaveAttribute('open');
  expect(container.textContent).toContain('max_iterations = 3');
  expect(screen.queryByText(/暂不支持/)).not.toBeInTheDocument();
  expect(JSON.stringify(graph)).toBe(before);
});

it('renders nested containers and the full node path with the selected role binding', () => {
  const graph = nestedFixture();
  graph.nodes.find((node) => node.node_id === 'draft-1-1')!.assignee = { type: 'bot_binding', binding: 'writer' };
  const { container } = render(<CollaborationFlowPreview graph={graph} initialNodes={['prepare-1']}
    bindingViews={{ writer: { roleName: '撰稿角色', botId: 'bot-1', botName: '撰稿 Bot' } }} highlightedBinding="writer" />);
  expect(screen.getByTestId('loop-container:outer/inner')).toHaveAttribute('data-parent', 'loop-container:outer');
  expect(screen.getByTestId('draft-1-1')).toHaveAttribute('data-parent', 'loop-container:outer/inner');
  const node = screen.getByRole('group', { name: /撰稿 Bot.*路径 outer 1\/2 → inner 1\/2/ });
  expect(node).toHaveAttribute('data-loop-path', 'outer/inner');
  expect(node).toHaveClass('ring-primary/20');
  expect(screen.getByText('撰稿 Bot')).toBeInTheDocument();
  expect(container.querySelectorAll('[data-loop-route="continue"]')).toHaveLength(2);
  expect(container.textContent).toContain('Inner（outer/inner）：max_iterations = 2');
});

it('keeps ordinary DAG previews free of loop controls and reports unsupported graphs', () => {
  const graph: CollaborationDefinitionGraphPreview = { graph_mode: 'acyclic', nodes: [
    { node_id: 'work', display_name: '普通工作', kind: 'bot_task', final_output: true, judge: false },
  ], edges: [] };
  const { rerender } = render(<CollaborationFlowPreview graph={graph} initialNodes={['work']} bindingViews={{}} />);
  expect(screen.getByText('普通工作')).toBeInTheDocument();
  expect(screen.queryByText('循环限制')).not.toBeInTheDocument();
  rerender(<CollaborationFlowPreview graph={{ ...graph, graph_mode: 'hierarchical' }} initialNodes={['work']} bindingViews={{}} />);
  expect(screen.getByText('暂不支持 hierarchical 模式的协作流程预览')).toBeInTheDocument();
});
