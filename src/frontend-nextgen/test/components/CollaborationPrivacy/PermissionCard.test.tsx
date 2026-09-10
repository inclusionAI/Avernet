/** @jest-environment jsdom */
import { PermissionCard } from '@/components/CollaborationPrivacy/PermissionCard';
import type { CollaborationBot } from '@/domain/collaborationPrivacy/types';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const bot: CollaborationBot = {
  id: 'bot-1',
  name: '协作助手',
  engine: 'OpenClaw',
  joinedBcn: true,
  collaborationStatus: 'online',
  profilePublic: true,
  taskClaimingEnabled: true,
  dreamModelEnabled: false,
  publication: {
    user: { scope: 'all', organizationPaths: [] },
    bot: { scope: 'all', organizationPaths: [] },
  },
  pendingPublications: {},
  friendApproval: { mode: 'all', exemptOrganizationPaths: [] },
};

function renderCard(cardBot: CollaborationBot = bot) {
  const onToggleDirect = jest.fn();
  const onRefresh = jest.fn();
  render(
    <PermissionCard
      bot={cardBot}
      busyAction={null}
      onCopyId={jest.fn()}
      onRefresh={onRefresh}
      onToggleDirect={onToggleDirect}
      onEditPublication={jest.fn()}
      onEditFriendApproval={jest.fn()}
      onViewScope={jest.fn()}
      onViewFriendApprovalScope={jest.fn()}
    />,
  );
  return { onRefresh, onToggleDirect };
}

describe('PermissionCard', () => {
  it('distinguishes section labels from concrete setting titles', () => {
    renderCard();

    const capabilityHeading = screen.getByRole('heading', { level: 4, name: '协作能力' });
    const publicationHeading = screen.getByRole('heading', { level: 4, name: 'Bot 可见性' });
    const cardColumns = capabilityHeading.closest('section')?.parentElement;

    expect(capabilityHeading).toHaveClass('text-xs', 'text-muted-foreground', 'tracking-wide');
    expect(publicationHeading).toHaveClass('text-xs', 'text-muted-foreground', 'tracking-wide');
    expect(cardColumns).toHaveClass('grid', 'lg:grid-cols-2', 'gap-6');
    expect(cardColumns?.lastElementChild).toHaveClass('lg:border-l', 'lg:pl-6');
    expect(capabilityHeading.parentElement).toHaveClass('mb-3');
    expect(publicationHeading.parentElement).toHaveClass('mb-3');
    expect(screen.getByText('参与协作群聊').closest('div.flex.items-start')).toHaveClass('first:pt-0');
    expect(capabilityHeading.parentElement).not.toHaveClass('border-b');
    expect(publicationHeading.parentElement).not.toHaveClass('border-b');
    expect(screen.getByText('参与协作群聊')).toHaveClass('text-sm', 'text-foreground');
    expect(screen.getByText('对用户可见性')).toHaveClass('text-sm', 'text-foreground');
    expect(screen.getByRole('heading', { level: 4, name: 'Bot 好友审批' })).toBeInTheDocument();
    expect(screen.getByText('好友审批策略')).toHaveClass('text-sm', 'text-foreground');
  });

  it('only shows verified Bot identity fields in the card header', () => {
    renderCard();

    expect(screen.getByRole('heading', { name: '协作助手' })).toHaveAttribute('title', '协作助手');
    expect(screen.getByText('Bot UUID')).toBeInTheDocument();
    expect(screen.getByTitle('bot-1')).toHaveTextContent('bot-1');
    expect(screen.getByRole('button', { name: '复制 协作助手 的 Bot UUID' })).toBeEnabled();
    expect(screen.getByText('OpenClaw')).toBeInTheDocument();
  });

  it('explains Bot visibility from the group label', async () => {
    const user = userEvent.setup();
    renderCard();

    await user.hover(screen.getByRole('button', { name: 'Bot 可见性功能说明' }));

    expect(
      await screen.findByText(
        '分别控制其他用户和其他 Bot 能否在协作广场看到当前 Bot，并决定其是否可以发起好友申请。两个对象的可见性可单独设置。',
      ),
    ).toBeInTheDocument();
  });

  it('explains Bot friend approval from the group label without duplicate row tooltips', async () => {
    const user = userEvent.setup();
    renderCard();

    await user.hover(screen.getByRole('button', { name: 'Bot 好友审批功能说明' }));

    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip).toHaveTextContent(
      '在其他用户或其他 Bot 发起好友申请后，统一控制是否需要审批。待审批的申请可前往「管理后台 / 通知中心 / 待我处理」处理。',
    );
    expect(screen.getByRole('link', { name: '管理后台 / 通知中心 / 待我处理' })).toHaveAttribute(
      'href',
      '/admin?tab=work-orders',
    );
    expect(screen.queryByRole('button', { name: '对用户可见性说明' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '对 Bot 可见性说明' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '好友审批策略说明' })).not.toBeInTheDocument();
  });

  it('keeps refresh and copy actions close to their related Bot information', () => {
    renderCard();

    const title = screen.getByRole('heading', { name: '协作助手' });
    const refreshButton = screen.getByRole('button', { name: '刷新 协作助手 的权限状态' });
    const uuidLabel = screen.getByText('Bot UUID');
    const uuidValue = screen.getByTitle('bot-1');
    const copyButton = screen.getByRole('button', { name: '复制 协作助手 的 Bot UUID' });

    expect(title.parentElement).toContainElement(refreshButton);
    expect(title.parentElement).toHaveClass('items-center', 'gap-2');
    expect(title.parentElement).not.toHaveClass('justify-between');
    expect(uuidLabel.parentElement).toContainElement(copyButton);
    expect(uuidValue).not.toHaveClass('flex-1');
    expect(uuidValue).toHaveClass('max-w-[48rem]');
    expect(uuidValue.nextElementSibling).toBe(copyButton);
  });

  it('refreshes only this Bot on demand instead of adding detail requests to page initialization', () => {
    const { onRefresh } = renderCard();

    fireEvent.click(screen.getByRole('button', { name: '刷新 协作助手 的权限状态' }));

    expect(onRefresh).toHaveBeenCalledWith(bot);
  });

  it('marks an unavailable Bot profile capability and disables only its switch', () => {
    renderCard({ ...bot, profilePublic: false, profilePublicStatus: 'unavailable' });

    expect(screen.getByText('暂不可用')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: '关闭任务认领' })).toBeEnabled();
    expect(screen.getByRole('switch', { name: '开启Dream Mode' })).toBeEnabled();
    expect(screen.getByRole('switch', { name: '开启Bot 画像公开' })).toBeDisabled();
    expect(
      screen.getByText('控制当前 Bot 是否可参与群聊。关闭后无法加入新协作群，已加入的协作群也不再回复。'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('允许其他用户在群聊中通过「融合模式」查看公开画像并进行跨 Bot 增量洞察。'),
    ).toBeInTheDocument();
    expect(screen.getByText('开启后，Bot 将每天自动扫描任务广场并认领可执行的任务。')).toBeInTheDocument();
    expect(
      screen.getByText('开启后，Bot 将每天基于用户数据（语雀、会议纪要等）挖掘潜在任务并推送。'),
    ).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: '关闭参与协作群聊' })).toBeEnabled();
    expect(screen.getByRole('switch', { name: '开启Bot 画像公开' })).toBeDisabled();
    expect(screen.getByText('Bot 画像公开')).toBeInTheDocument();
    expect(screen.getByText('Bot 可见性')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Bot 可见性功能说明' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Bot 好友审批功能说明' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '对用户可见性说明' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '对 Bot 可见性说明' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '好友审批策略说明' })).not.toBeInTheDocument();
    expect(screen.getByText('好友审批策略')).toBeInTheDocument();
  });

  it('explains why an unavailable Bot profile visibility config must be initialized first', async () => {
    const user = userEvent.setup();
    renderCard({ ...bot, profilePublic: false, profilePublicStatus: 'unavailable' });

    const status = screen.getByText('暂不可用');
    const statusTrigger = screen.getByRole('button', { name: 'Bot 画像公开暂不可用原因' });
    const profileSwitch = screen.getByRole('switch', { name: '开启Bot 画像公开' });
    expect(status).not.toHaveRole('button');
    expect(statusTrigger.parentElement).toContainElement(profileSwitch);
    await user.hover(statusTrigger);

    expect(await screen.findByRole('tooltip')).toHaveTextContent('该 Bot 尚未对其他 Bot 开放可见，请先调整 Bot 可见性');
    expect(profileSwitch).toBeDisabled();
  });

  it('restores the Bot profile visibility toggle and sends the confirmed target value', () => {
    const { onToggleDirect } = renderCard();

    fireEvent.click(screen.getByRole('switch', { name: '关闭Bot 画像公开' }));

    expect(onToggleDirect).toHaveBeenCalledWith(bot, 'profilePublic', false);
  });
});
