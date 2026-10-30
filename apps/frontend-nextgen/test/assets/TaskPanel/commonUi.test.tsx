/** @jest-environment jsdom */
// TaskPanel 通用 UI 组件的分支覆盖。
import { renderSafeNode, SafeHtml } from '@/assets/TaskPanel/SafeHtml';
import { Empty, GlobalKeyframes, LabelValue, SectionCard, Segmented, StatusTag } from '@/assets/TaskPanel/theme';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, jest } from '@jest/globals';

const renderAll = () => {
  render(
    <>
      <GlobalKeyframes />
      <Empty description="暂无步骤" minHeight={100} />
      <StatusTag status="EXECUTING" />
      <StatusTag status="UNKNOWN_STATUS" />
      <LabelValue label="任务类型" value="工作流" />
      <LabelValue label="结束时间" value={null} />
      <SectionCard title="节点详情" marginTop={16}>
        children
      </SectionCard>
      <SectionCard title="默认分隔">default</SectionCard>
      <SafeHtml html={'<p>paragraph</p><a title="link" href="https://example.com">good</a><a href="javascript:alert(1)">bad</a><img src="/safe.png" alt="image" />'} className="safe-html" />
    </>,
  );
};

describe('TaskPanel common UI', () => {
  it('renders all components and covers optional branches', () => {
    renderAll();

    expect(screen.getByText('暂无步骤')).toBeInTheDocument();
    expect(screen.getByText('执行中')).toBeInTheDocument();
    expect(screen.getByText('定义中')).toBeInTheDocument();
    expect(screen.getByText('任务类型')).toBeInTheDocument();
    expect(screen.getByText('工作流')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.getByText('节点详情')).toBeInTheDocument();
    expect(screen.getByText('children')).toBeInTheDocument();
    expect(screen.getByText('default')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'good' })).toHaveAttribute('href', 'https://example.com');
    expect(screen.getByRole('img', { name: 'image' })).toHaveAttribute('src', '/safe.png');
    expect(screen.getByText('bad').closest('a')).not.toHaveAttribute('href');
  });

  it('selects segmented options', () => {
    const onChange = jest.fn();
    render(
      <Segmented
        value="node"
        onChange={onChange}
        options={[
          { label: '节点', value: 'node' },
          { label: 'DAG', value: 'dag' },
        ]}
      />,
    );

    expect(screen.getByRole('button', { name: '节点' })).toHaveAttribute('style');
    fireEvent.click(screen.getByRole('button', { name: 'DAG' }));

    expect(onChange).toHaveBeenCalledWith('dag');
  });
  it('covers SafeHtml node filtering and guarded media attributes', () => {
    render(
      <SafeHtml
        className="safe-html-extra"
        html={
          '<!-- hidden note -->' +
          '<span>正文</span>' +
          '<script>alert(1)</script>' +

          '<img src="javascript:alert(1)" alt="unsafe" />' +
          '<img src="/safe-image.png" />'
        }
      />,
    );

    expect(screen.getByText('正文')).toBeInTheDocument();
    expect(screen.getByText('alert(1)')).toBeInTheDocument();
    expect(screen.getByAltText('unsafe')).not.toHaveAttribute('src');
    expect(screen.getByAltText('')).toHaveAttribute('src', '/safe-image.png');
  });

  it('renders comment nodes as null and empty text nodes as empty strings', () => {
    const comment = document.createComment('hidden');
    expect(renderSafeNode(comment, 'comment-key')).toBeNull();

    const emptyText = { nodeType: Node.TEXT_NODE, textContent: null } as unknown as Text;
    expect(renderSafeNode(emptyText, 'text-key')).toBe('');

    const table = document.createElement('table');
    table.appendChild(document.createTextNode('   '));
    expect(renderSafeNode(table, 'table-key')).toBeDefined();
  });

});
