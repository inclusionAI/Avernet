/** @jest-environment jsdom */
// PanelAppend 渲染层单测：块顺序/追加可见、replace 只留最后一块、空态、子组件 tab 身份。
import { render } from '@testing-library/react';
import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent';
import { PanelAppend } from '@/components/PanelAppend';
import { registerPanelComponent } from '@/services/workspace/panelComponentRegistry';

function host(params: PanelContentProps['params']): PanelContentProps {
  return {
    params,
    payload: params,
    onAction: jest.fn(),
    onInteraction: jest.fn(),
    tab: { id: 'task-app', type: 'panel-append', title: '副屏', closable: true } as PanelContentProps['tab'],
  };
}

function contents(container: HTMLElement): (string | null)[] {
  return Array.from(container.querySelectorAll('.panel-append-content')).map((node) => node.textContent);
}

it('append 按 blocks 顺序渲染 A、B，B 在 A 下方（AC-1）', () => {
  const { container } = render(
    <PanelAppend
      {...host({
        mode: 'append',
        blocks: [{ content: 'A' }, { content: 'B' }],
      })}
    />,
  );
  expect(contents(container)).toEqual(['A', 'B']);
  expect(container.querySelector('.panel-append')?.getAttribute('data-panel-append-mode')).toBe('append');
});

it('replace 即使误传多块也只渲染最后一块（AC-4）', () => {
  const { container } = render(
    <PanelAppend
      {...host({
        mode: 'replace',
        blocks: [{ content: 'A' }, { content: 'B' }],
      })}
    />,
  );
  expect(contents(container)).toEqual(['B']);
  expect(container.querySelector('.panel-append')?.getAttribute('data-panel-append-mode')).toBe('replace');
});

it('缺省 mode 等价 replace，只渲染最后一块（AC-2）', () => {
  const { container } = render(
    <PanelAppend {...host({ blocks: [{ content: 'A' }, { content: 'B' }] })} />,
  );
  expect(contents(container)).toEqual(['B']);
});

it('空 blocks 显示空态，不渲染内容块', () => {
  const { container } = render(<PanelAppend {...host({ mode: 'append', blocks: [] })} />);
  expect(container.querySelector('.panel-append-empty')?.textContent).toBe('暂无内容');
  expect(contents(container)).toEqual([]);
});

it('组件块派发时 tab 身份还原为子组件与 task-<taskId>，不是 panel-append 宿主（P3）', () => {
  function Probe(props: PanelContentProps) {
    return (
      <div
        data-probe="1"
        data-tab-type={props.tab.type}
        data-tab-id={props.tab.id}
        data-task-id={String(props.params.taskId ?? '')}
      />
    );
  }
  registerPanelComponent('test.Probe', Probe);
  const { container } = render(
    <PanelAppend
      {...host({
        mode: 'append',
        blocks: [{ component: 'test.Probe', params: { taskId: 't1' } }],
      })}
    />,
  );
  const probe = container.querySelector('[data-probe="1"]');
  expect(probe?.getAttribute('data-tab-type')).toBe('test.Probe');
  expect(probe?.getAttribute('data-tab-id')).toBe('task-t1');
  expect(probe?.getAttribute('data-task-id')).toBe('t1');
});
