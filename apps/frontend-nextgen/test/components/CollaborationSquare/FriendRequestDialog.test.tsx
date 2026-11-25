/** @jest-environment jsdom */
import { FriendRequestDialog } from '@/components/CollaborationSquare/FriendRequestDialog';
import type { PublicBot, PublicBotProfile } from '@/domain/collaborationSquare/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';

const bot: PublicBot = {
  id: '20250812_reviewer:300403',
  name: '虾摸鱼',
  ownerName: '风太(123123)',
  description: '个人提效助手，擅长信息检索与摘要整理。',
  capabilities: [],
  relationshipStatus: 'none',
};

function profileWith(overrides: Partial<PublicBotProfile> = {}): PublicBotProfile {
  return {
    id: bot.id,
    name: bot.name,
    ownerName: bot.ownerName,
    description: bot.description,
    engine: 'OpenClaw',
    capabilities: [],
    skills: [],
    ...overrides,
  };
}

function renderDialog(overrides: { profile?: PublicBotProfile | null } = {}) {
  return render(
    <FriendRequestDialog
      bot={bot}
      profile={overrides.profile ?? null}
      onClose={jest.fn()}
      onSendRequest={jest.fn()}
    />,
  );
}

describe('FriendRequestDialog 无已公开能力（dmore artboard-003）', () => {
  test('580px 弹窗：72px 头像 + 名称/引擎胶囊 + Owner/UUID 元信息行', () => {
    renderDialog({ profile: profileWith({ engine: undefined }) });

    const dialog = screen.getByRole('dialog');
    // 无能力版弹窗 580px 宽；白底 12px 圆角、无描边、无默认内边距。
    expect(dialog).toHaveClass('max-w-[580px]', 'rounded-xl', 'border-0', 'p-0');
    // 头像改为圆形真实头像（Avatar 组件，无 avatarUrl 时回退首字母占位）。
    const avatar = dialog.querySelector('span[aria-label="虾摸鱼"]')?.closest('div');
    expect(avatar).toHaveClass('h-[72px]', 'w-[72px]', 'rounded-full', 'bg-primary/10');
    expect(dialog.querySelector('svg.lucide-bot')).toBeNull();
    expect(within(dialog).getByText('虾摸鱼')).toHaveClass('text-lg', 'font-semibold');
    const meta = within(dialog).getByText('风太(123123)').closest('div[class*="gap-3"]');
    // Owner 与 UUID 两段间 1px 竖分隔线（dmore Line 1）。
    expect(meta).toHaveClass('text-xs', 'text-muted-foreground');
    expect(meta?.querySelector('span[aria-hidden].h-4.w-px')).not.toBeNull();
    expect(within(dialog).getByText(bot.id)).toBeInTheDocument();
  });

  test('引擎胶囊为 15% 灰底、10px 圆角、12px 字号', () => {
    renderDialog({ profile: profileWith() });

    const chip = screen.getByText('OpenClaw');
    expect(chip).toHaveClass('h-[22px]', 'rounded-[10px]', 'bg-foreground/15', 'text-xs', 'font-medium');
  });

  test('「申请好友权限」按钮水平居中沉底：110×32 蓝底 8px 圆角', () => {
    renderDialog({ profile: profileWith({ engine: undefined }) });

    const button = screen.getByRole('button', { name: '申请好友权限' });
    expect(button).toHaveClass('h-8', 'w-[110px]', 'rounded-lg', 'text-[13px]');
    const buttonRow = button.closest('div');
    expect(buttonRow).toHaveClass('flex', 'justify-center', 'pb-8');
  });

  test('点击「申请好友权限」发送申请', () => {
    const onSendRequest = jest.fn();
    render(
      <FriendRequestDialog bot={bot} profile={null} onClose={jest.fn()} onSendRequest={onSendRequest} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '申请好友权限' }));
    expect(onSendRequest).toHaveBeenCalledTimes(1);
  });

  test('描述为空时降级「暂无描述」', () => {
    render(<FriendRequestDialog bot={{ ...bot, description: '' }} profile={null} onClose={jest.fn()} onSendRequest={jest.fn()} />);
    expect(screen.getByText('暂无描述')).toBeInTheDocument();
  });

  test('画像拉取失败（404 已取消公开）在弹窗内提示，不再走 sonner toast', () => {
    render(
      <FriendRequestDialog
        bot={bot}
        profile={null}
        profileError="内容已取消公开或不可访问"
        onClose={jest.fn()}
        onSendRequest={jest.fn()}
      />,
    );
    const alert = screen.getByRole('alert');
    // 提示而非错误：中性 muted 色，不用红色 destructive。
    expect(alert).toHaveClass('text-xs', 'text-muted-foreground');
    expect(alert).toHaveTextContent('无法获取公开画像：内容已取消公开或不可访问');
    // 画像失败不关弹窗：无能力版式仍可发起申请。
    expect(screen.getByRole('button', { name: '申请好友权限' })).toBeInTheDocument();
  });

  test('画像接口加载中：镜像能力清单的 shimmer 占位，隐藏发送按钮', () => {
    render(
      <FriendRequestDialog
        bot={bot}
        profile={null}
        profileLoading
        onClose={jest.fn()}
        onSendRequest={jest.fn()}
      />,
    );
    // Catalog 摘要（描述）先行展示，占位不顶掉描述行。
    expect(screen.getByText(bot.description)).toBeInTheDocument();
    // 占位镜像真实版式：标题行 + 两行 62px 行卡片，结构对齐避免加载后版式跳变。
    const placeholder = screen.getByLabelText('正在加载公开画像');
    expect(within(placeholder).getByText('已公开能力')).toBeInTheDocument();
    const rows = placeholder.querySelectorAll('li');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveClass('flex', 'h-[62px]', 'rounded-[10px]', 'bg-muted');
    // 头部引擎胶囊以等几何 shimmer 占位（22px 高、10px 圆角）。
    expect(document.querySelector('.animate-pulse.h-\\[22px\\]')).not.toBeNull();
    // 加载中隐藏发送按钮、不展示失败提示。
    expect(screen.queryByRole('button', { name: '申请好友权限' })).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  test('无画像失败时不渲染弹窗内错误提示', () => {
    renderDialog({ profile: null });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

describe('FriendRequestDialog 有已公开能力（dmore artboard-007）', () => {
  const capabilities = Array.from({ length: 10 }, (_, index) => ({
    id: `cap-${index}`,
    name: ['AntV 可视化图表', 'Dima MCP', '工作报告撰写'][index % 3],
  }));

  test('682px 弹窗：按钮挂头部右端，不沉底', () => {
    renderDialog({ profile: profileWith({ capabilities }) });

    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveClass('max-w-[682px]');
    const button = within(dialog).getByRole('button', { name: '申请好友权限' });
    const header = button.closest('div');
    // 头部行按钮 ml-auto 右挂；弹窗内不再有沉底按钮行（justify-center）。
    expect(header).toHaveClass('ml-auto', 'shrink-0');
    expect(within(dialog).queryByText('已公开能力')).toBeInTheDocument();
    // 头部按钮不沉底：按钮父级不是「居中沉底」按钮行。
    expect(button.parentElement).not.toHaveClass('justify-center', 'pb-8');
  });

  test('「已公开能力」标题行右侧展示能力计数', () => {
    renderDialog({ profile: profileWith({ capabilities }) });

    expect(screen.getByText('已公开能力')).toHaveClass('text-[13px]', 'font-medium');
    expect(screen.getByText('10 个MCP')).toHaveClass('text-[11px]', 'text-muted-foreground');
  });

  test('能力行 62px 高软灰底：28px 白底图标位 + 能力名 + MCP 类型胶囊', () => {
    renderDialog({ profile: profileWith({ capabilities }) });

    const rows = screen.getAllByText(/AntV 可视化图表|Dima MCP|工作报告撰写/);
    for (const name of rows) {
      const row = name.closest('li');
      expect(row).toHaveClass('flex', 'h-[62px]', 'rounded-[10px]', 'bg-muted');
      const iconSlot = row?.querySelector('span.h-7.w-7');
      expect(iconSlot).toHaveClass('rounded-[7px]', 'bg-card');
    }
    const chips = screen.getAllByText('MCP');
    expect(chips).toHaveLength(10);
    for (const chip of chips) {
      expect(chip).toHaveClass('text-[10px]', 'font-normal');
      expect(chip).toHaveClass('bg-success/10', 'text-success');
    }
  });

  test('MCP 与 Skill 类型胶囊区分：MCP 行在前、Skill 行在后，计数双段展示', () => {
    renderDialog({
      profile: profileWith({
        capabilities: [
          { id: 'cap-1', name: 'AntV 可视化图表' },
          { id: 'cap-2', name: 'Dima MCP' },
        ],
        skills: [{ id: 'skill-1', name: '风太周报' }],
      }),
    });

    // 纯技能场景也走 682px 有能力版式；计数行只列非零段并以「 · 」分隔。
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveClass('max-w-[682px]');
    expect(screen.getByText('2 个MCP · 1 个Skill')).toBeInTheDocument();
    // 合并清单顺序：MCP 行在前、Skill 行在后。
    const list = dialog.querySelector('ul');
    expect(list).not.toBeNull();
    const names = [...(list as HTMLUListElement).querySelectorAll('li')].map(
      (row) => row.querySelector('span:nth-of-type(2)')?.textContent,
    );
    expect(names).toEqual(['AntV 可视化图表', 'Dima MCP', '风太周报']);
    // 类型胶囊：MCP 绿（success）、Skill 蓝（primary），几何一致。
    expect(screen.getAllByText('MCP')[0]).toHaveClass('bg-success/10', 'text-success');
    expect(screen.getByText('Skill')).toHaveClass('bg-primary/10', 'text-primary');
    expect(screen.getByText('Skill')).toHaveClass('text-[10px]', 'font-normal');
  });

  test('仅有 Skill（无 MCP）同样按有能力版式展示，计数只列 Skill 段', () => {
    renderDialog({
      profile: profileWith({ capabilities: [], skills: [{ id: 'skill-1', name: '风太周报' }] }),
    });

    expect(screen.getByRole('dialog')).toHaveClass('max-w-[682px]');
    expect(screen.getByText('1 个Skill')).toBeInTheDocument();
    expect(screen.getByText('已公开能力')).toBeInTheDocument();
  });

  test('能力区间顶部有 24px 内缩分割线', () => {
    renderDialog({ profile: profileWith({ capabilities }) });
    const dialog = screen.getByRole('dialog');
    const divider = dialog.querySelector('span[aria-hidden].h-px.bg-foreground\\/10');
    expect(divider).not.toBeNull();
    expect(divider).toHaveClass('mx-6', 'mt-5');
  });
});
