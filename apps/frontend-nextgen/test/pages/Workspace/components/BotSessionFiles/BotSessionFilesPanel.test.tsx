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

describe('BotSessionFilesPanel（单聊会话文件右侧内嵌面板）', () => {
  it('副屏容器语义：统一面板头（标题 + 会话名副标题 + 关闭按钮），无 Modal 弹窗语义', () => {
    render(
      <BotSessionFilesPanel
        sessionName="重构验收OC"
        readyFiles={readyFiles}
        isLoadingList={false}
        botId="bot-1"
        sessionId="s1"
        userId="u1"
        ownerId="u1"
        onClose={jest.fn()}
        onUploadClick={jest.fn()}
        onOpen={jest.fn()}
        onDelete={jest.fn()}
        onDownload={jest.fn()}
        onReference={jest.fn()}
      />,
    );

    expect(screen.getByRole('heading', { name: '会话文件' })).toBeInTheDocument();
    expect(screen.getByText('重构验收OC')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '关闭管理面板' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('列表视图常驻（计数 + 上传文件 + 单聊特有「引用到输入框」行操作），初始无预览', () => {
    render(
      <BotSessionFilesPanel
        sessionName="重构验收OC"
        readyFiles={readyFiles}
        isLoadingList={false}
        botId="bot-1"
        sessionId="s1"
        userId="u1"
        ownerId="u1"
        onClose={jest.fn()}
        onUploadClick={jest.fn()}
        onOpen={jest.fn()}
        onDelete={jest.fn()}
        onDownload={jest.fn()}
        onReference={jest.fn()}
      />,
    );

    expect(screen.getByText('会话文件（1）')).toBeInTheDocument();
    expect(screen.getAllByText('手法还原结果.json').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole('button', { name: '上传文件' })).toBeInTheDocument();
    // 单聊特有：引用到输入框（群聊面板无此项）。
    expect(screen.getByRole('button', { name: '引用到输入框' })).toBeInTheDocument();
    // 下钻模式：打开即纯列表，无预览占位。
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();
    expect(screen.queryByText('选择一个文件开始预览')).not.toBeInTheDocument();
  });

  it('下钻交互：点击行内「预览文件」按钮切到预览视图，行本体点击无动作', () => {
    render(
      <BotSessionFilesPanel
        sessionName="重构验收OC"
        readyFiles={readyFiles}
        isLoadingList={false}
        botId="bot-1"
        sessionId="s1"
        userId="u1"
        ownerId="u1"
        onClose={jest.fn()}
        onUploadClick={jest.fn()}
        onOpen={jest.fn()}
        onDelete={jest.fn()}
        onDownload={jest.fn()}
        onReference={jest.fn()}
      />,
    );

    // 点击行本体不触发下钻（行恢复纯展示）。
    fireEvent.click(screen.getAllByText('手法还原结果.json')[0]);
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();

    // 点击行内 [预览] 按钮（操作组首位）→ 下钻到预览视图。
    fireEvent.click(screen.getByRole('button', { name: '预览文件 手法还原结果.json' }));
    expect(screen.getByTestId('session-files-preview')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回文件列表' })).toBeInTheDocument();
    // 合并头契约：预览头同时承载返回导航与文件操作（单聊无分享，仅下载）。
    expect(screen.getByRole('button', { name: '下载' })).toBeInTheDocument();

    // 返回 → 回到列表视图。
    fireEvent.click(screen.getByRole('button', { name: '返回文件列表' }));
    expect(screen.getByText('会话文件（1）')).toBeInTheDocument();
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();
  });

  it('不可预览类型：预览按钮禁用并提示原因，点击不下钻', () => {
    const files: BotSessionFileView[] = [
      { resourceId: 'r1', displayName: '手法还原结果.json', sizeBytes: 2048, status: 'ready', errorCode: null },
      { resourceId: 'r2', displayName: '打包资料.zip', sizeBytes: 4096, status: 'ready', errorCode: null },
    ];
    render(
      <BotSessionFilesPanel
        sessionName="重构验收OC"
        readyFiles={files}
        isLoadingList={false}
        botId="bot-1"
        sessionId="s1"
        userId="u1"
        ownerId="u1"
        onClose={jest.fn()}
        onUploadClick={jest.fn()}
        onOpen={jest.fn()}
        onDelete={jest.fn()}
        onDownload={jest.fn()}
        onReference={jest.fn()}
      />,
    );

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

  it('类型过滤 Tab：按大类聚合带计数，点击后仅展示该类文件', () => {
    const files: BotSessionFileView[] = [
      { resourceId: 'r1', displayName: '手法还原结果.json', sizeBytes: 2048, status: 'ready', errorCode: null },
      { resourceId: 'r2', displayName: '说明文档.md', sizeBytes: 1024, status: 'ready', errorCode: null },
      { resourceId: 'r3', displayName: '打包资料.zip', sizeBytes: 4096, status: 'ready', errorCode: null },
    ];
    render(
      <BotSessionFilesPanel
        sessionName="重构验收OC"
        readyFiles={files}
        isLoadingList={false}
        botId="bot-1"
        sessionId="s1"
        userId="u1"
        ownerId="u1"
        onClose={jest.fn()}
        onUploadClick={jest.fn()}
        onOpen={jest.fn()}
        onDelete={jest.fn()}
        onDownload={jest.fn()}
        onReference={jest.fn()}
      />,
    );

    // Tab 动态生成：全部 + 实际存在的大类（带计数）。
    expect(screen.getByRole('tab', { name: '全部 3' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '代码 1' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '文档 1' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '其他 1' })).toBeInTheDocument();

    // 点击「代码」→ 仅显示 json，文档与压缩不显示。
    fireEvent.click(screen.getByRole('tab', { name: '代码 1' }));
    expect(screen.getByText('手法还原结果.json')).toBeInTheDocument();
    expect(screen.queryByText('说明文档.md')).not.toBeInTheDocument();
    expect(screen.queryByText('打包资料.zip')).not.toBeInTheDocument();
  });

  it('文件行统一模型：两行结构（文件名 + 大小元数据行），操作按钮常显', () => {
    render(
      <BotSessionFilesPanel
        sessionName="重构验收OC"
        readyFiles={readyFiles}
        isLoadingList={false}
        botId="bot-1"
        sessionId="s1"
        userId="u1"
        ownerId="u1"
        onClose={jest.fn()}
        onUploadClick={jest.fn()}
        onOpen={jest.fn()}
        onDelete={jest.fn()}
        onDownload={jest.fn()}
        onReference={jest.fn()}
      />,
    );

    // 单聊次行元数据：暂缺上传者/时间（后端接口未返回），仅展示大小——
    // 两行结构模型与群聊侧同构，后端补齐字段后元数据行自动扩充。
    expect(screen.getByText('2.0 KB')).toBeInTheDocument();
    // 操作按钮常显：按钮组容器不再保留 hover 悬浮的透明/禁点击切换
    // （行级断言，避免误伤 Empty 组件装饰光晕等无关 pointer-events 用途）。
    const actions = screen.getByRole('button', { name: '下载文件' }).parentElement;
    expect(actions?.className).not.toContain('opacity-0');
    expect(actions?.className).not.toContain('pointer-events-none');
  });

  it('副屏保持打开时切换会话：再次触发列表刷新（避免清空后不重载）', () => {
    const onOpen = jest.fn();
    const baseProps = {
      sessionName: '重构验收OC',
      readyFiles,
      isLoadingList: false,
      botId: 'bot-1',
      userId: 'u1',
      ownerId: 'u1',
      onClose: jest.fn(),
      onUploadClick: jest.fn(),
      onDelete: jest.fn(),
      onDownload: jest.fn(),
      onReference: jest.fn(),
    };
    const { rerender } = render(<BotSessionFilesPanel {...baseProps} sessionId="s1" onOpen={onOpen} />);

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
