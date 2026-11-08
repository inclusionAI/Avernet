/** @jest-environment jsdom */
import { BotSubpageShell } from '@/components/BotWorkshop/BotSubpageShell';
import '@testing-library/jest-dom';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { render, screen } from '@testing-library/react';

describe('BotSubpageShell 子页壳', () => {
  it('顶栏呈现返回、Bot 头像、标题、副标题与 actions，内容区透传 children', () => {
    render(
      <BotSubpageShell
        onBack={jest.fn()}
        botName="PPT助手"
        title="日志 · PPT助手"
        subtitle="Bot ID：abc123 · 查询对话 Trace"
        actions={<button type="button">刷新结果</button>}
      >
        <div>内容插槽</div>
      </BotSubpageShell>,
    );
    expect(screen.getByRole('button', { name: '返回 Bot 管理' })).toBeInTheDocument();
    expect(document.querySelector('div[aria-label="PPT助手 头像"]')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '日志 · PPT助手' })).toBeInTheDocument();
    expect(screen.getByText('Bot ID：abc123 · 查询对话 Trace')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新结果' })).toBeInTheDocument();
    expect(screen.getByText('内容插槽')).toBeInTheDocument();
  });

  it('副题为空时不渲染副题行', () => {
    render(
      <BotSubpageShell onBack={jest.fn()} botName="PPT助手" title="任务实例">
        <div>内容</div>
      </BotSubpageShell>,
    );
    expect(screen.queryByText(/Bot ID：/)).not.toBeInTheDocument();
  });

  it('顶栏与画布使用 shadcn 语义 token，无 var(--color-*) 遗产引用', () => {
    const source = readFileSync(
      join(process.cwd(), 'src/components/BotWorkshop/BotSubpageShell/index.tsx'),
      'utf-8',
    );
    expect(source).not.toContain('var(--color-');
    render(
      <BotSubpageShell botName="B" title="T" onBack={jest.fn()}>
        <div>x</div>
      </BotSubpageShell>,
    );
    const header = screen.getByRole('button', { name: '返回 Bot 管理' }).closest('header');
    expect(Array.from(header?.classList ?? [])).toEqual(expect.arrayContaining(['bg-card', 'border-b', 'border-border']));
    const canvas = header?.nextElementSibling;
    expect(Array.from(canvas?.classList ?? [])).toEqual(expect.arrayContaining(['bg-page-canvas']));
  });
});