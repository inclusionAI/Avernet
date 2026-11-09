/** @jest-environment jsdom */
/**
 * BotSessionFilesRow 元数据行（2026-11-09 创建者信息契约）：
 * 「来源+创建者 · 创建时间 · 大小」三段逐段拼接，任一段缺省省略连带分隔点，
 * 全缺省回退现状仅大小；创建者信息不参与预览/删除等行为门控。
 * 文案与老前端（oc-web）add-session-file-creator-fields 逐字对齐。
 */
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { BotSessionFilesRow } from '@/pages/Workspace/components/BotSessionFiles/BotSessionFilesRow';
import { TooltipProvider } from '@/components/ui/Tooltip';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

const noop = () => {};
const actions = {
  onOpenPreview: noop,
  onReference: noop,
  onDownload: noop,
  onDelete: noop,
};

function view(patch: Partial<BotSessionFileView>): BotSessionFileView {
  return {
    resourceId: 'r1',
    displayName: 'report.txt',
    status: 'ready',
    sizeBytes: 1024,
    errorCode: null,
    creatorType: null,
    creatorName: null,
    createdAt: null,
    ...patch,
  };
}

// Row 内的 Tooltip 正常由面板层 TooltipProvider 承载，独立渲染须自包一层。
function renderRow(file: BotSessionFileView, actionsOverride: Partial<typeof actions> = {}) {
  return render(
    <TooltipProvider>
      <BotSessionFilesRow file={file} {...actions} {...actionsOverride} />
    </TooltipProvider>,
  );
}

describe('BotSessionFilesRow 元数据行 - 创建者信息三段展示', () => {
  it('Bot 生成带名：来源合段 + 创建时间 + 大小逐段拼接', () => {
    renderRow(view({ creatorType: 'bot', creatorName: '报告助手', createdAt: '2026-11-09T02:15:30.123456Z' }));
    // 三段一段不缺：UTC 锚时间（不随查看端时区漂移）。
    expect(screen.getByText('Bot 生成 · 报告助手 · 2026-11-09 02:15:30 · 1.0 KB')).toBeInTheDocument();
  });

  it('用户上传名缺失：只显来源标签，无追加分隔名/占位符', () => {
    renderRow(view({ creatorType: 'user', creatorName: '   ' }));
    expect(screen.getByText('用户上传 · 1.0 KB')).toBeInTheDocument();
  });

  it('来源未知（creatorType null / 未来枚举）：整段不渲染，不猜测来源', () => {
    const { rerender } = renderRow(view({ creatorName: '某名字' }));
    expect(screen.getByText('1.0 KB')).toBeInTheDocument();

    rerender(
      <TooltipProvider>
        <BotSessionFilesRow
          file={view({ creatorType: 'whatever-new' as unknown as BotSessionFileView['creatorType'] })}
          {...actions}
        />
      </TooltipProvider>,
    );
    expect(screen.getByText('1.0 KB')).toBeInTheDocument();
    expect(screen.queryByText(/Bot 生成|用户上传|未知/)).not.toBeInTheDocument();
  });

  it('时间缺省不回退：来源段 + 大小，无时间段无占位', () => {
    renderRow(view({ creatorType: 'user', creatorName: '运海' }));
    expect(screen.getByText('用户上传 · 运海 · 1.0 KB')).toBeInTheDocument();
    expect(screen.queryByText(/--/)).not.toBeInTheDocument();
  });

  it('legacy 三字段全 null：回退现状仅大小', () => {
    renderRow(view({}));
    expect(screen.getByText('1.0 KB')).toBeInTheDocument();
  });

  it('携带创建者信息不影响预览/删除动作可用性', () => {
    const onOpenPreview = jest.fn();
    renderRow(view({ creatorType: 'bot', creatorName: '报告助手', displayName: 'a.png' }), { onOpenPreview });
    // 可预览类型行操作照常可用（创建者信息仅展示，不做门控）。
    expect(screen.getByRole('button', { name: '预览文件 a.png' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '删除文件' })).toBeEnabled();
  });
});
