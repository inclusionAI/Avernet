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
    const publicationHeading = screen.getByRole('heading', { level: 4, name: '公开范围' });
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
    expect(screen.getByText('其他用户可添加为好友')).toHaveClass('text-sm', 'text-foreground');
  });

  it('only shows verified Bot identity fields in the card header', () => {
    renderCard();

    expect(screen.getByRole('heading', { name: '协作助手' })).toHaveAttribute('title', '协作助手');
    expect(screen.getByText('Bot UUID')).toBeInTheDocument();
    expect(screen.getByTitle('bot-1')).toHaveTextContent('bot-1');
    expect(screen.getByRole('button', { name: '复制 协作助手 的 Bot UUID' })).toBeEnabled();
    expect(screen.getByText('OpenClaw')).toBeInTheDocument();
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
    expect(screen.getByText('公开范围')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '公开范围说明' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '其他用户可添加为好友说明' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '其他 Bot 可添加为好友说明' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '好友审批策略说明' })).toBeInTheDocument();
    expect(screen.getByText('好友审批策略')).toBeInTheDocument();
  });

  it('explains why an unavailable Bot profile visibility config must be initialized first', async () => {
    const user = userEvent.setup();
    renderCard({ ...bot, profilePublic: false, profilePublicStatus: 'unavailable' });

    expect(screen.getByText('暂不可用')).toBeInTheDocument();
    const statusTrigger = screen.getByRole('button', { name: 'Bot 画像公开暂不可用说明' });
    await user.hover(statusTrigger);

    expect(await screen.findByRole('tooltip')).toHaveTextContent(
      '该Bot暂未设置过允许其他Bot可添加好友，请先调整公开范围',
    );
    expect(screen.getByRole('switch', { name: '开启Bot 画像公开' })).toBeDisabled();
  });

  it('restores the Bot profile visibility toggle and sends the confirmed target value', () => {
    const { onToggleDirect } = renderCard();

    fireEvent.click(screen.getByRole('switch', { name: '关闭Bot 画像公开' }));

    expect(onToggleDirect).toHaveBeenCalledWith(bot, 'profilePublic', false);
  });
});
