/** @jest-environment jsdom */
import { MarkdownCell } from '@/assets/TaskPanel/MarkdownCell';
import { describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

// 未覆盖分支补充:空内容占位「—」、折叠/展开交互(展开全部 ↔ 收起)。

describe('MarkdownCell 空内容与展开交互', () => {
  it('内容为 null/空白 → 渲染占位「—」,不产 markdown 视图与展开按钮', () => {
    const { container } = render(
      <>
        <MarkdownCell content={null} />
        <MarkdownCell content="   " />
      </>,
    );
    expect(screen.getAllByText('—')).toHaveLength(2);
    expect(container.querySelector('button')).toBeNull();
  });

  it('默认折叠:点击「展开全部」切换展开,再点「收起」折叠回来', () => {
    render(
      <MarkdownCell
        content={'# 标题甲\n\n- 列表项一\n- 列表项二\n\n段落正文内容。'}
        fontSize={12}
      />,
    );
    // markdown 体渲染保留层级(SafeHtml 白名单节点)
    expect(screen.getByRole('heading', { level: 1, name: '标题甲' })).toBeInTheDocument();
    expect(screen.getByText('列表项一')).toBeInTheDocument();
    // 折叠态只出现「展开全部」
    expect(screen.getByRole('button', { name: '展开全部 ▾' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '收起 ▴' })).toBeNull();
    // 展开 → 出现「收起」
    fireEvent.click(screen.getByRole('button', { name: '展开全部 ▾' }));
    expect(screen.getByRole('button', { name: '收起 ▴' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '展开全部 ▾' })).toBeNull();
    // 收起 → 回到折叠态
    fireEvent.click(screen.getByRole('button', { name: '收起 ▴' }));
    expect(screen.getByRole('button', { name: '展开全部 ▾' })).toBeInTheDocument();
  });
});
