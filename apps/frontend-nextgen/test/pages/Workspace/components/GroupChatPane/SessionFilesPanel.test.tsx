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

describe('SessionFilesPanel（群聊「资源管理」右侧副屏，与单聊侧同构）', () => {
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

  it('副屏容器语义：统一面板头（标题「资源管理」+ 关闭按钮），无 Modal 弹窗语义', () => {
    render(<SessionFilesPanel sessionId="s1" participants={participants} onClose={jest.fn()} />);

    // 面板头与单聊侧 BotSessionFilesPanel 同构（48px 标题行 + 关闭 X）。
    expect(screen.getByRole('heading', { name: '资源管理' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '关闭资源管理面板' })).toBeInTheDocument();
    // 不再出现 Modal 弹窗语义（dialog role）。
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('列表视图常驻（筛选条 + 上传文件图标钮入口），初始不渲染任何预览', () => {
    render(<SessionFilesPanel sessionId="s1" participants={participants} onClose={jest.fn()} />);

    // 筛选条形态对齐单聊侧：下划线 Tabs（无计数）+ 图标式上传钮（aria-label 承载名称）。
    expect(screen.getByRole('tab', { name: '全部' })).toBeInTheDocument();
    expect(screen.getAllByText('分析报告.pdf').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole('button', { name: '上传文件' })).toBeInTheDocument();
    // 下钻模式：打开即纯列表，无自动选中、无预览占位。
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();
    expect(screen.queryByText('选择一个文件开始预览')).not.toBeInTheDocument();
  });

  it('文件行统一模型：两行结构（文件名 + 上传者·时间·大小元数据行），操作按钮悬停显现', () => {
    render(<SessionFilesPanel sessionId="s1" participants={participants} onClose={jest.fn()} />);

    // 元数据行：上传者 · 时间 · 大小（与单聊侧同构模型，群聊侧数据更全）。
    expect(screen.getByText(/^风太 · \d{2}\/\d{2} \d{2}:\d{2} · 2\.0 KB$/)).toBeInTheDocument();
    // 行操作对齐单聊侧设计稿：静息隐藏、悬停/聚焦显现（常驻挂载 opacity 切换，无布局位移，
    // 也无 pointer-events 切换——后者才是 Tooltip 闪烁缺陷的历史对策）。
    const actions = screen.getByRole('button', { name: '下载文件' }).parentElement;
    expect(actions?.className).toContain('opacity-0');
    expect(actions?.className).toContain('group-hover:opacity-100');
    expect(actions?.className).not.toContain('pointer-events-none');
  });

  it('下钻交互：点击文件行本体直接进入预览视图，行内「预览」按钮入口保留', () => {
    render(<SessionFilesPanel sessionId="s1" participants={participants} onClose={jest.fn()} />);

    // 直接点击文件名（行本体）→ 下钻到预览视图（与单聊侧 2026-10-12 验收拍板一致，
    // 不再强制用户点行内「预览」按钮）。
    fireEvent.click(screen.getAllByText('分析报告.pdf')[0]);
    expect(screen.getByTestId('session-files-preview')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回文件列表' })).toBeInTheDocument();
    // 合并头契约：预览头同时承载返回导航与文件操作（群聊特有分享 + 下载）。
    expect(screen.getByRole('button', { name: '分享' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载' })).toBeInTheDocument();

    // 返回 → 回到列表视图，行内 [预览] 按钮（操作组首位）仍可下钻。
    fireEvent.click(screen.getByRole('button', { name: '返回文件列表' }));
    expect(screen.getByRole('tab', { name: '全部' })).toBeInTheDocument();
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '预览文件 分析报告.pdf' }));
    expect(screen.getByTestId('session-files-preview')).toBeInTheDocument();
  });

  it('不可预览类型：预览按钮禁用并提示原因，行本体纯展示，点击不下钻', () => {
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
    render(<SessionFilesPanel sessionId="s1" participants={participants} onClose={jest.fn()} />);

    // 非图片/PDF/文本白名单类型：预览按钮携带禁用语义与原因提示。
    // 采用 aria-disabled 模式（非原生 disabled）：禁用态仍需 hover 出 Tooltip
    // 提示原因，而原生 disabled button 不派发 pointer 事件会让 Tooltip 失效。
    const disabledBtn = screen.getByRole('button', { name: '预览文件 打包资料.zip（该文件类型暂不支持预览）' });
    expect(disabledBtn).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(disabledBtn);
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();

    // 行本体非可预览文件时仍为纯展示容器：点击无动作，无重复入口制造「能预览」假象。
    fireEvent.click(screen.getAllByText('打包资料.zip')[0]);
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();

    // 可预览类型：按钮名不带禁用提示，行本体点击直接下钻。
    const enabledBtn = screen.getByRole('button', { name: '预览文件 分析报告.pdf' });
    expect(enabledBtn).not.toHaveAttribute('aria-disabled');
    fireEvent.click(screen.getAllByText('分析报告.pdf')[0]);
    expect(screen.getByTestId('session-files-preview')).toBeInTheDocument();
  });

  it('类型筛选 Tab：设计稿形态（无计数），按大类动态生成，点击后仅展示该类文件', () => {
    filesMock.files = [
      { fileId: 'f1', name: '分析报告.pdf', size: 2048, status: 'ready', ownerName: '风太', createdAt: 1789380000 },
      { fileId: 'f2', name: '说明文档.md', size: 1024, status: 'ready', ownerName: '风太', createdAt: 1789380100 },
      { fileId: 'f3', name: '截图.png', size: 512, status: 'ready', ownerName: '风太', createdAt: 1789380200 },
      { fileId: 'f4', name: '打包资料.zip', size: 4096, status: 'ready', ownerName: '风太', createdAt: 1789380300 },
    ];
    filesMock.total = 4;
    render(<SessionFilesPanel sessionId="s1" participants={participants} onClose={jest.fn()} />);

    // Tab 动态生成（无计数装饰，对齐单聊侧）：全部 + 实际存在的大类。
    expect(screen.getByRole('tab', { name: '全部' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '文档' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '媒体' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '其他' })).toBeInTheDocument();
    // 无代码/表格文件则不出对应 tab。
    expect(screen.queryByRole('tab', { name: /代码/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /表格/ })).not.toBeInTheDocument();

    // 点击「文档」→ 仅显示 pdf/md，媒体与压缩不显示。
    fireEvent.click(screen.getByRole('tab', { name: '文档' }));
    expect(screen.getByText('分析报告.pdf')).toBeInTheDocument();
    expect(screen.getByText('说明文档.md')).toBeInTheDocument();
    expect(screen.queryByText('截图.png')).not.toBeInTheDocument();
    expect(screen.queryByText('打包资料.zip')).not.toBeInTheDocument();

    // 切回「全部」→ 恢复全量。
    fireEvent.click(screen.getByRole('tab', { name: '全部' }));
    expect(screen.getByText('截图.png')).toBeInTheDocument();
    expect(screen.getByText('打包资料.zip')).toBeInTheDocument();
  });

  it('空列表：仅列表空态（对齐单聊侧文案），无预览占位', () => {
    filesMock.files = [];
    filesMock.total = 0;
    render(<SessionFilesPanel sessionId="s1" participants={participants} onClose={jest.fn()} />);
    expect(screen.getByText('暂无资源')).toBeInTheDocument();
    expect(screen.queryByTestId('session-files-preview')).not.toBeInTheDocument();
    expect(screen.queryByText('选择一个文件开始预览')).not.toBeInTheDocument();
  });

  it('原 Modal 契约不再适用：不渲染单「文件」标签页头', () => {
    render(<SessionFilesPanel sessionId="s1" participants={participants} onClose={jest.fn()} />);
    // 冗余的单标签页头（原 SessionFilesModal 的「文件」tab）已移除。
    expect(screen.queryByText('文件', { selector: 'div' })).not.toBeInTheDocument();
  });
});
