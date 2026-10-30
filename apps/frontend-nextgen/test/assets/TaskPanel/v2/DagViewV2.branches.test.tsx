/** @jest-environment jsdom */
// readonly-exec DAG 的布局兜底、非法边过滤和返工边样式分支。
import { DagViewV2 } from '@/assets/TaskPanel/v2/DagViewV2';
import type { ExecDagEdge } from '@/assets/TaskPanel/statusDerivation';
import { describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

const step = (id: string, label: string) => ({ id, label });

describe('DagViewV2 readonly-exec 分支', () => {
  it('没有执行步骤时不渲染只读 DAG', () => {
    const { container } = render(<DagViewV2 view="readonly-exec" steps={[]} stepEdges={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('引用不存在节点的边不会渲染连线', () => {
    const steps = [step('s-1', '执行')];
    const stepEdges = [
      { from: 'missing-from', to: 's-1', semantics: 'rework' as const },
      { from: 's-1', to: 'missing-to', semantics: 'none' as const },
    ];
    const { container } = render(<DagViewV2 view="readonly-exec" steps={steps} stepEdges={stepEdges} />);
    const view = screen.getByTestId('exec-dag-view');

    expect(view).toBeInTheDocument();
    expect(container.querySelectorAll('path')).toHaveLength(0);
  });

  it('长步骤名截断,返工边使用 warning 虚线并降低透明度', () => {
    const steps = [step('s-1', '开始'), step('s-2', 'ABCDEFGHIJKLMNOPQ')];
    const stepEdges: ExecDagEdge[] = [{ from: 's-1', to: 's-2', semantics: 'rework' }];
    const { container } = render(<DagViewV2 view="readonly-exec" steps={steps} stepEdges={stepEdges} />);
    const path = container.querySelector<SVGPathElement>('path');

    expect(screen.getByText('ABCDEFGHIJKLMNOP…')).toBeInTheDocument();
    expect(path).not.toBeNull();
    expect(path!.getAttribute('stroke')).toBe('#FF7D00');
    expect(path!.getAttribute('stroke-dasharray')).toBe('4 3');
    expect(path!.getAttribute('opacity')).toBe('0.9');
  });
  it('同层分支和已有层级的入边使用最长路径兜底', () => {
    const steps = [step('s-a', '开始'), step('s-b', '调研'), step('s-c', '评审')];
    const stepEdges = [
      { from: 's-a', to: 's-b', semantics: 'none' as const },
      { from: 's-a', to: 's-c', semantics: 'none' as const },
    ];
    const { container } = render(
      <DagViewV2 view="readonly-exec" steps={steps} stepEdges={stepEdges} />,
    );

    expect(container.querySelectorAll('path')).toHaveLength(2);
    expect(screen.getByText('开始')).toBeInTheDocument();
    expect(screen.getByText('调研')).toBeInTheDocument();
    expect(screen.getByText('评审')).toBeInTheDocument();
  });
  it('重复入边会先回到最长路径表的已有层，再在入度清零时入队', () => {
    const steps = [step('s-root', '开始'), step('s-work', '处理')];
    const stepEdges = [
      { from: 's-root', to: 's-work', semantics: 'none' as const },
      { from: 's-root', to: 's-work', semantics: 'none' as const },
    ];
    const { container } = render(
      <DagViewV2 view="readonly-exec" steps={steps} stepEdges={stepEdges} />,
    );

    expect(container.querySelectorAll('path')).toHaveLength(2);
    expect(screen.getByText('开始')).toBeInTheDocument();
    expect(screen.getByText('处理')).toBeInTheDocument();
  });
});
