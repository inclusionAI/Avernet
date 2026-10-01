/** @jest-environment jsdom */
import type { ParticipantView } from '@/domain/collaboration';
import { SessionFilesPanel } from '@/pages/Workspace/components/GroupChatPane/SessionFilesPanel';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

// 列表数据可切换（有文件 / 空列表），锁定「未选中不渲染预览」契约需要空态场景。
const filesMock = { files: [] as Array<Record<string, unknown>>, total: 0 };
jest.mock('@/pages/Workspace/hooks/useSessionFiles', () => ({
  useSessionFiles: () => filesMock,
}));
jest.mock('@/pages/Workspace/hooks/useSessionFileUpload', () => ({
  useSessionFileUpload: () => ({
    queue: [],
    isUploading: false,
    stageFiles: jest.fn(),
    submitStaged: jest.fn(async () => {}),
    cancelTask: jest.fn(),
    retryTask: jest.fn(),
    discardAll: jest.fn(),
    clearCompleted: jest.fn(),
    hasPending: false,
  }),
}));
jest.mock('@/pages/Workspace/hooks/useSessionFilePreview', () => ({
  useSessionFilePreview: () => ({ kind: 'pdf', status: 'loading' }),
}));

const participants: ParticipantView[] = [
  { actorId: 'human_1', kind: 'human', name: '风太', role: 'driver', mode: 'present' },
];

describe('SessionFilesPanel（会话文件右侧内嵌面板）', () => {
  beforeEach(() => {
    // 默认有 1 个就绪文件（供选中联动用例）；空态用例内自行置空。
    filesMock.files = [
      {
        fileId: 'f1',
        name: '分析报告.pdf',
        size: 2048,
        status: 'ready',
        ownerName: '风太',
        createdAt: 1789380000,
      },
    ];
    filesMock.total = 1;
  });

  it('使用副屏容器语义：面板头标题 + 会话名副标题 + 可访问关闭按钮', () => {
    render(
      <SessionFilesPanel sessionId="s1" sessionName="迭代排期会话" participants={participants} onClose={jest.fn()} />,
    );

    // 面板头与群/会话管理面板同构（ManagePanelHeader 语义）。
    expect(screen.getByRole('heading', { name: '会话文件' })).toBeInTheDocument();
    expect(screen.getByText('迭代排期会话')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '关闭管理面板' })).toBeInTheDocument();
    // 不再出现 Modal 弹窗语义（dialog role）。
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('列表视图常驻（计数 + 上传文件入口），初始不渲染任何预览', () => {
    render(
      <SessionFilesPanel sessionId="s1" sessionName="迭代排期会话" participants={participants} onClose={jest.fn()} />,
    );

    // 列表内核保留：计数文本与文件行。
    expect(screen.getByText('会话文件（1）')).toBeInTheDocument();
    expect(screen.getAllByText('分析报告.pdf').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole('button', { name: '上传文件' })).toBeInTheDocument();
    // 下钻模式：打开即纯列表，无自动选中、无预览占位。
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();
    expect(screen.queryByText('选择一个文件开始预览')).not.toBeInTheDocument();
  });

  it('文件行统一模型：两行结构（文件名 + 上传者·时间·大小元数据行），操作按钮常显', () => {
    render(
      <SessionFilesPanel sessionId="s1" sessionName="迭代排期会话" participants={participants} onClose={jest.fn()} />,
    );

    // 元数据行：上传者 · 时间 · 大小（大小并入次行，与单聊侧同构模型）。
    expect(screen.getByText(/^风太 · \d{2}\/\d{2} \d{2}:\d{2} · 2\.0 KB$/)).toBeInTheDocument();
    // 操作按钮常显：按钮组容器不再保留 hover 悬浮的透明/禁点击切换
    // （行级断言，避免误伤 Empty 组件装饰光晕等无关 pointer-events 用途）。
    const actions = screen.getByRole('button', { name: '下载文件' }).parentElement;
    expect(actions?.className).not.toContain('opacity-0');
    expect(actions?.className).not.toContain('pointer-events-none');
  });

  it('下钻交互：点击行内「预览文件」按钮整屏切到预览视图，返回按钮回到列表', () => {
    render(
      <SessionFilesPanel sessionId="s1" sessionName="迭代排期会话" participants={participants} onClose={jest.fn()} />,
    );

    // 点击行本体不触发下钻（行恢复纯展示，预览入口收敛到行内操作按钮）。
    fireEvent.click(screen.getByText('分析报告.pdf'));
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();

    // 点击行内 [预览] 按钮（操作组首位）→ 下钻到预览视图（带返回导航，预览独占副屏）。
    fireEvent.click(screen.getByRole('button', { name: '预览文件 分析报告.pdf' }));
    expect(screen.getByTestId('session-files-preview')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回文件列表' })).toBeInTheDocument();
    // 合并头契约：预览头同时承载返回导航与文件操作（分享/下载），无独立导航层。
    expect(screen.getByRole('button', { name: '分享' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载' })).toBeInTheDocument();
    // 列表视图被替换（计数行不再渲染）。
    expect(screen.queryByText('会话文件（1）')).not.toBeInTheDocument();

    // 返回 → 回到列表视图。
    fireEvent.click(screen.getByRole('button', { name: '返回文件列表' }));
    expect(screen.getByText('会话文件（1）')).toBeInTheDocument();
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();
  });

  it('不可预览类型：预览按钮禁用并提示原因，点击不下钻', () => {
    filesMock.files = [
      {
        fileId: 'f1',
        name: '分析报告.pdf',
        size: 2048,
        status: 'ready',
        ownerName: '风太',
        createdAt: 1789380000,
      },
      {
        fileId: 'f2',
        name: '打包资料.zip',
        size: 4096,
        status: 'ready',
        ownerName: '风太',
        createdAt: 1789380100,
      },
    ];
    filesMock.total = 2;
    render(
      <SessionFilesPanel sessionId="s1" sessionName="迭代排期会话" participants={participants} onClose={jest.fn()} />,
    );

    // 非图片/PDF/文本白名单类型：预览按钮携带禁用语义与原因提示。
    // 采用 aria-disabled 模式（非原生 disabled）：禁用态仍需 hover 出 Tooltip
    // 提示原因，而原生 disabled button 不派发 pointer 事件会让 Tooltip 失效。
    const disabledBtn = screen.getByRole('button', { name: '预览文件 打包资料.zip（该文件类型暂不支持预览）' });
    expect(disabledBtn).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(disabledBtn);
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();

    // 可预览类型：按钮名不带禁用提示，无禁用语义，点击仍可下钻。
    const enabledBtn = screen.getByRole('button', { name: '预览文件 分析报告.pdf' });
    expect(enabledBtn).not.toHaveAttribute('aria-disabled');
    fireEvent.click(enabledBtn);
    expect(screen.getByTestId('session-files-preview')).toBeInTheDocument();
  });

  it('类型过滤 Tab：按大类聚合带计数，点击后仅展示该类文件', () => {
    filesMock.files = [
      { fileId: 'f1', name: '分析报告.pdf', size: 2048, status: 'ready', ownerName: '风太', createdAt: 1789380000 },
      { fileId: 'f2', name: '说明文档.md', size: 1024, status: 'ready', ownerName: '风太', createdAt: 1789380100 },
      { fileId: 'f3', name: '截图.png', size: 512, status: 'ready', ownerName: '风太', createdAt: 1789380200 },
      { fileId: 'f4', name: '打包资料.zip', size: 4096, status: 'ready', ownerName: '风太', createdAt: 1789380300 },
    ];
    filesMock.total = 4;
    render(
      <SessionFilesPanel sessionId="s1" sessionName="迭代排期会话" participants={participants} onClose={jest.fn()} />,
    );

    // Tab 动态生成：全部（总计数）+ 实际存在的大类（带各自信数）。
    expect(screen.getByRole('tab', { name: '全部 4' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '文档 2' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '媒体 1' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '其他 1' })).toBeInTheDocument();
    // 无代码/表格文件则不出对应 tab。
    expect(screen.queryByRole('tab', { name: /代码/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /表格/ })).not.toBeInTheDocument();

    // 点击「文档」→ 仅显示 pdf/md，媒体与压缩不显示。
    fireEvent.click(screen.getByRole('tab', { name: '文档 2' }));
    expect(screen.getByText('分析报告.pdf')).toBeInTheDocument();
    expect(screen.getByText('说明文档.md')).toBeInTheDocument();
    expect(screen.queryByText('截图.png')).not.toBeInTheDocument();
    expect(screen.queryByText('打包资料.zip')).not.toBeInTheDocument();

    // 切回「全部」→ 恢复全量。
    fireEvent.click(screen.getByRole('tab', { name: '全部 4' }));
    expect(screen.getByText('截图.png')).toBeInTheDocument();
    expect(screen.getByText('打包资料.zip')).toBeInTheDocument();
  });

  it('空列表：仅列表空态，无预览占位', () => {
    filesMock.files = [];
    filesMock.total = 0;
    render(
      <SessionFilesPanel sessionId="s1" sessionName="迭代排期会话" participants={participants} onClose={jest.fn()} />,
    );
    expect(screen.getByText('暂无会话文件')).toBeInTheDocument();
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();
    expect(screen.queryByText('选择一个文件开始预览')).not.toBeInTheDocument();
  });

  it('原 Modal 契约不再适用：不渲染单「文件」标签页头', () => {
    render(
      <SessionFilesPanel sessionId="s1" sessionName="迭代排期会话" participants={participants} onClose={jest.fn()} />,
    );
    // 冗余的单标签页头（原 SessionFilesModal 的「文件」tab）已移除。
    expect(screen.queryByText('文件', { selector: 'div' })).not.toBeInTheDocument();
  });
});
