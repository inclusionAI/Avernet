/** @jest-environment jsdom */
import { BotSessionFilesPanel } from '@/pages/Workspace/components/BotSessionFiles/BotSessionFilesPanel';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/pages/Workspace/hooks/useBotSessionFilePreview', () => ({
  useBotSessionFilePreview: () => ({ kind: 'other', status: 'unsupported' }),
}));

const readyFiles: BotSessionFileView[] = [
  {
    resourceId: 'r1',
    displayName: '手法还原结果.json',
    sizeBytes: 2048,
    status: 'ready',
    errorCode: null,
  },
];

const baseProps = {
  readyFiles,
  isLoadingList: false,
  botId: 'bot-1',
  sessionId: 's1',
  userId: 'u1',
  ownerId: 'u1',
  onClose: jest.fn(),
  onUploadClick: jest.fn(),
  onOpen: jest.fn(),
  onDelete: jest.fn(),
  onDownload: jest.fn(),
  onReference: jest.fn(),
};

describe('BotSessionFilesPanel（单聊「资源管理」右侧副屏）', () => {
  it('副屏容器语义：统一面板头（标题「资源管理」+ 关闭按钮），无 Modal 弹窗语义', () => {
    render(<BotSessionFilesPanel {...baseProps} />);

    expect(screen.getByRole('heading', { name: '资源管理' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '关闭资源管理面板' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('列表视图常驻（筛选条 + 上传文件 + 单聊特有「引用到输入框」行操作），初始无预览', () => {
    render(<BotSessionFilesPanel {...baseProps} />);

    expect(screen.getByRole('tab', { name: '全部' })).toBeInTheDocument();
    expect(screen.getAllByText('手法还原结果.json').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole('button', { name: '上传文件' })).toBeInTheDocument();
    // 单聊特有：引用到输入框（群聊面板无此项）。
    expect(screen.getByRole('button', { name: '引用到输入框' })).toBeInTheDocument();
    // 下钻模式：打开即纯列表，无预览占位。
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();
    expect(screen.queryByText('选择一个文件开始预览')).not.toBeInTheDocument();
  });

  it('下钻交互：点击文件行本体直接进入预览视图，行内「预览」按钮入口保留', () => {
    render(<BotSessionFilesPanel {...baseProps} />);

    // 直接点击文件名（行本体）→ 下钻到预览视图（2026-10-12 验收拍板，
    // 替代原「行本体纯展示、仅预览图标可下钻」的交互）。
    fireEvent.click(screen.getAllByText('手法还原结果.json')[0]);
    expect(screen.getByTestId('session-files-preview')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回文件列表' })).toBeInTheDocument();
    // 合并头契约：预览头同时承载返回导航与文件操作（单聊无分享，含引用/下载）。
    expect(screen.getByRole('button', { name: '引用' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载' })).toBeInTheDocument();

    // 返回 → 回到列表视图，行内 [预览] 按钮（操作组首位）仍可下钻。
    fireEvent.click(screen.getByRole('button', { name: '返回文件列表' }));
    expect(screen.getByRole('tab', { name: '全部' })).toBeInTheDocument();
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '预览文件 手法还原结果.json' }));
    expect(screen.getByTestId('session-files-preview')).toBeInTheDocument();
  });

  it('预览视图引用：点击「引用」按钮透传 onReference（关闭面板/插入输入框由上层处理）', () => {
    const onReference = jest.fn();
    render(<BotSessionFilesPanel {...baseProps} onReference={onReference} />);

    fireEvent.click(screen.getAllByText('手法还原结果.json')[0]);
    fireEvent.click(screen.getByRole('button', { name: '引用' }));
    expect(onReference).toHaveBeenCalledTimes(1);
  });

  it('不可预览类型：预览按钮禁用并提示原因，点击不下钻', () => {
    const files: BotSessionFileView[] = [
      { resourceId: 'r1', displayName: '手法还原结果.json', sizeBytes: 2048, status: 'ready', errorCode: null },
      { resourceId: 'r2', displayName: '打包资料.zip', sizeBytes: 4096, status: 'ready', errorCode: null },
    ];
    render(<BotSessionFilesPanel {...baseProps} readyFiles={files} />);

    // 非图片/PDF/文本白名单类型：预览按钮携带禁用语义与原因提示。
    // 采用 aria-disabled 模式（非原生 disabled）：禁用态仍需 hover 出 Tooltip
    // 提示原因，而原生 disabled button 不派发 pointer 事件会让 Tooltip 失效。
    const disabledBtn = screen.getByRole('button', { name: '预览文件 打包资料.zip（该文件类型暂不支持预览）' });
    expect(disabledBtn).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(disabledBtn);
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();

    // 可预览类型：按钮名不带禁用提示，无禁用语义，点击仍可下钻。
    const enabledBtn = screen.getByRole('button', { name: '预览文件 手法还原结果.json' });
    expect(enabledBtn).not.toHaveAttribute('aria-disabled');
    fireEvent.click(enabledBtn);
    expect(screen.getByTestId('session-files-preview')).toBeInTheDocument();
  });

  it('不可预览类型：行本体也保持纯展示，点击文件名不下钻', () => {
    const files: BotSessionFileView[] = [
      { resourceId: 'r2', displayName: '打包资料.zip', sizeBytes: 4096, status: 'ready', errorCode: null },
    ];
    render(<BotSessionFilesPanel {...baseProps} readyFiles={files} />);

    // 行本体非可预览文件时仍为纯展示容器：点击无动作，无重复入口制造「能预览」假象。
    fireEvent.click(screen.getAllByText('打包资料.zip')[0]);
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();
  });

  it('类型筛选 Tab：设计稿形态（无计数），按大类动态生成，点击后仅展示该类文件', () => {
    const files: BotSessionFileView[] = [
      { resourceId: 'r1', displayName: '手法还原结果.json', sizeBytes: 2048, status: 'ready', errorCode: null },
      { resourceId: 'r2', displayName: '说明文档.md', sizeBytes: 1024, status: 'ready', errorCode: null },
      { resourceId: 'r3', displayName: '打包资料.zip', sizeBytes: 4096, status: 'ready', errorCode: null },
    ];
    render(<BotSessionFilesPanel {...baseProps} readyFiles={files} />);

    // Tab 动态生成（无计数装饰）：全部 + 实际存在的大类。
    expect(screen.getByRole('tab', { name: '全部' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '代码' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '文档' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '其他' })).toBeInTheDocument();

    // 点击「代码」→ 仅显示 json，文档与压缩不显示。
    fireEvent.click(screen.getByRole('tab', { name: '代码' }));
    expect(screen.getByText('手法还原结果.json')).toBeInTheDocument();
    expect(screen.queryByText('说明文档.md')).not.toBeInTheDocument();
    expect(screen.queryByText('打包资料.zip')).not.toBeInTheDocument();
  });

  it('文件行统一模型：两行结构（文件名 + 大小元数据行），操作按钮悬停显现', () => {
    render(<BotSessionFilesPanel {...baseProps} />);

    // 单聊次行元数据：暂缺上传者/时间（后端接口未返回），仅展示大小——
    // 设计稿（artboard-008）为「风太 · 09/15 14:22 · 7.0KB」，后端补齐字段后本行扩充。
    expect(screen.getByText('2.0 KB')).toBeInTheDocument();
    // 行操作对齐设计稿：静息隐藏、悬停/聚焦显现（常驻挂载 opacity 切换，无布局位移，
    // 也无 pointer-events 切换——后者才是 Tooltip 闪烁缺陷的历史对策）。
    const actions = screen.getByRole('button', { name: '下载文件' }).parentElement;
    expect(actions?.className).toContain('opacity-0');
    expect(actions?.className).toContain('group-hover:opacity-100');
    expect(actions?.className).not.toContain('pointer-events-none');
  });

  it('副屏保持打开时切换会话：再次触发列表刷新（避免清空后不重载）', () => {
    const onOpen = jest.fn();
    const { rerender } = render(<BotSessionFilesPanel {...baseProps} onOpen={onOpen} />);

    // 副屏挂载 == 打开，触发一次刷新。
    expect(onOpen).toHaveBeenCalledTimes(1);

    // 副屏未关闭、组件不卸载时切换会话：sessionId 变化应再次触发刷新
    // （修复回归点：切会话后列表被 resetForSession 清空，若刷新只在挂载时触发一次，
    //   新会话文件列表将永远不加载）。
    rerender(<BotSessionFilesPanel {...baseProps} sessionId="s2" onOpen={onOpen} />);
    expect(onOpen).toHaveBeenCalledTimes(2);

    // 同一会话内的无关重渲染（如列表数据更新）不重复触发刷新。
    rerender(
      <BotSessionFilesPanel
        {...baseProps}
        sessionId="s2"
        readyFiles={[
          { resourceId: 'r9', displayName: '新会话文件.json', sizeBytes: 512, status: 'ready', errorCode: null },
        ]}
        onOpen={onOpen}
      />,
    );
    expect(onOpen).toHaveBeenCalledTimes(2);
  });
});