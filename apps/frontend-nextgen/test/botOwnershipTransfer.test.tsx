/** @jest-environment jsdom */
/**
 * BotOwnershipTransferPanel — BCS ownership 交互（owner 发起 / manager 视角 / Human 收发件）：
 * - owner 视角可见「转交 ownership」并发起（snapshot ownership_version + 幂等 key）；
 * - manager 视角绝不渲染「转交 ownership」；
 * - 仅一 pending；409 ownership_changed → 刷新 ownership 快照并提示；
 * - 提交成功响应丢失 → 同 client_request_id 重试；
 * - DELETE 带 remaining_team_sources → 不把管理者/身份当已移除。
 * 面板只消费 botAuthorityService（ownership/version/pending），不自行推断当前 owner。
 */
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as ownershipController from '@/services/backendApi/collaboration/botOwnershipController';
import { BotOwnershipTransferPanel } from '@/components/BotOwnershipTransferPanel';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { IdentityView } from '@/domain/collaboration';
import { describe, expect, it, jest, beforeEach } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/botOwnershipController');
// mine（identityService）只为 403 刷新服务；本文件不关心其内容。
jest.mock('@/services/backendApi/collaboration/collaborationBotController');
jest.mock('@/services/backendApi/bots/botController');
jest.mock('@tc-chat/adapters', () => ({}));

const listMyBots = (jest.requireMock('@/services/backendApi/collaboration/collaborationBotController') as {
  listMyBots: jest.Mock<any>;
}).listMyBots;

const getBotOwnership = (ownershipController as unknown as { getBotOwnership: jest.Mock<any> })
  .getBotOwnership;
const listBotManagers = (ownershipController as unknown as { listBotManagers: jest.Mock<any> })
  .listBotManagers;
const revokeBotManager = (ownershipController as unknown as { revokeBotManager: jest.Mock<any> })
  .revokeBotManager;
const createBotOwnershipTransfer = (ownershipController as unknown as {
  createBotOwnershipTransfer: jest.Mock<any>;
}).createBotOwnershipTransfer;
const listBotOwnershipTransfers = (ownershipController as unknown as {
  listBotOwnershipTransfers: jest.Mock<any>;
}).listBotOwnershipTransfers;
const acceptBotOwnershipTransfer = (ownershipController as unknown as {
  acceptBotOwnershipTransfer: jest.Mock<any>;
}).acceptBotOwnershipTransfer;
const rejectBotOwnershipTransfer = (ownershipController as unknown as {
  rejectBotOwnershipTransfer: jest.Mock<any>;
}).rejectBotOwnershipTransfer;
const cancelBotOwnershipTransfer = (ownershipController as unknown as {
  cancelBotOwnershipTransfer: jest.Mock<any>;
}).cancelBotOwnershipTransfer;

const BOT_ID = 'bot-owned';

function okEnvelope(data: unknown, code = 20000) {
  return { code, message: '', request_id: 'r', data };
}

function ownershipEnvelope(overrides: Record<string, unknown> = {}) {
  return okEnvelope({ bot_id: BOT_ID, owner_user_id: 'user-a', ownership_version: 3, ...overrides });
}

function managersEnvelope(overrides: Record<string, unknown> = {}) {
  return okEnvelope({
    bot_id: BOT_ID,
    owner_user_id: 'user-a',
    items: [{ user_id: 'user-b', actor_id: 'human_user-b', role: 'manager' }],
    total: 1,
    offset: 0,
    limit: 20,
    ...overrides,
  });
}

function receiptsEnvelope(items: unknown[]) {
  return okEnvelope({ items, total: items.length, offset: 0, limit: 20 });
}

function receipt(overrides: Record<string, unknown> = {}) {
  return {
    transfer_id: 'transfer-1',
    bot_id: BOT_ID,
    from_user_id: 'user-a',
    to_user_id: 'user-b',
    status: 'pending',
    expected_owner_version: 3,
    expires_at: 4102444800000,
    bot_name_snapshot: 'Owned Bot',
    gmt_create: 1,
    gmt_modified: 1,
    ...overrides,
  };
}

beforeEach(() => {
  [
    getBotOwnership,
    listBotManagers,
    revokeBotManager,
    createBotOwnershipTransfer,
    listBotOwnershipTransfers,
    acceptBotOwnershipTransfer,
    rejectBotOwnershipTransfer,
    cancelBotOwnershipTransfer,
  ].forEach((mock) => mock.mockReset());
  listMyBots.mockReset();
  listMyBots.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: { items: [], total: 0, offset: 0, limit: 20 },
  });
  getBotOwnership.mockResolvedValue(ownershipEnvelope());
  listBotManagers.mockResolvedValue(managersEnvelope());
  listBotOwnershipTransfers.mockResolvedValue(receiptsEnvelope([]));

  const identities: IdentityView[] = [
    { id: 'human-1', kind: 'user', displayName: '示例用户', online: true },
    { id: BOT_ID, kind: 'bot', displayName: 'Owned Bot', online: true, accessRelation: 'owner' },
  ];
  useWorkspaceStore.getState().setIdentities(identities, 'human-1');
});

describe('Bot 视角：ownership 展示与转交发起', () => {
  it('owner 视角展示当前 ownership/version 并可发起转交', async () => {
    const user = userEvent.setup();
    createBotOwnershipTransfer.mockResolvedValue(okEnvelope(receipt()));
    render(<BotOwnershipTransferPanel mode="bot" botId={BOT_ID} accessRelation="owner" />);

    // 组件消费 service 拉的 ownership/version，不自造。
    await screen.findByText('当前 Owner：user-a');
    expect(screen.getByText('ownership 版本：3')).toBeInTheDocument();
    // 合同要求 acceptance/发起 UI 明示三条边界。
    expect(screen.getByText(/仅转移 BCS ownership/)).toBeInTheDocument();
    expect(screen.getByText(/原 owner 保留 manager/)).toBeInTheDocument();
    expect(screen.getByText(/部署与凭据不迁移/)).toBeInTheDocument();

    const initiate = screen.getByRole('button', { name: '转交 ownership' });
    await user.click(initiate);
    await user.type(await screen.findByLabelText('收件人 user_id'), 'user-b');
    await user.click(screen.getByRole('button', { name: '发起转交' }));

    await waitFor(() =>
      expect(createBotOwnershipTransfer).toHaveBeenCalledWith(
        BOT_ID,
        expect.objectContaining({ to_user_id: 'user-b', expected_owner_version: 3 }),
      ),
    );
    // 提交后拿到 receipt → 仅一 pending，不再渲染第二个发起入口。
    await screen.findByText(/转交待确认/);
    expect(screen.queryByRole('button', { name: '转交 ownership' })).not.toBeInTheDocument();
  });

  it('manager 视角不渲染「转交 ownership」发起（owner 才能发起）', async () => {
    render(<BotOwnershipTransferPanel mode="bot" botId={BOT_ID} accessRelation="manager" />);
    await screen.findByText('当前 Owner：user-a');
    expect(screen.queryByRole('button', { name: '转交 ownership' })).not.toBeInTheDocument();
    // manager 管理（Gate 0）可用。
    expect(await screen.findByLabelText('管理者 user_id')).toBeInTheDocument();
  });

  it('提交成功响应丢失后，同 client_request_id 重试', async () => {
    const user = userEvent.setup();
    createBotOwnershipTransfer
      .mockRejectedValueOnce(new Error('network dropped'))
      .mockResolvedValue(okEnvelope(receipt()));
    render(<BotOwnershipTransferPanel mode="bot" botId={BOT_ID} accessRelation="owner" />);

    await user.click(await screen.findByRole('button', { name: '转交 ownership' }));
    await user.type(await screen.findByLabelText('收件人 user_id'), 'user-b');
    await user.click(screen.getByRole('button', { name: '发起转交' }));
    await screen.findByText('ownership 转交提交失败，请重试');
    // response 丢失 ≠ 服务端未提交：重试必须带同一把幂等 key。
    await user.click(screen.getByRole('button', { name: '发起转交' }));

    await waitFor(() => expect(createBotOwnershipTransfer).toHaveBeenCalledTimes(2));
    const firstKey = createBotOwnershipTransfer.mock.calls[0][1].client_request_id;
    const retryKey = createBotOwnershipTransfer.mock.calls[1][1].client_request_id;
    expect(retryKey).toBe(firstKey);
    await screen.findByText(/转交待确认/);
  });

  it('409 ownership_changed 时刷新 ownership 并提示', async () => {
    const user = userEvent.setup();
    createBotOwnershipTransfer.mockRejectedValue({
      status: 409,
      data: { data: { error_code: 'ownership_changed' } },
    });
    render(<BotOwnershipTransferPanel mode="bot" botId={BOT_ID} accessRelation="owner" />);

    await user.click(await screen.findByRole('button', { name: '转交 ownership' }));
    await user.type(await screen.findByLabelText('收件人 user_id'), 'user-b');
    await user.click(screen.getByRole('button', { name: '发起转交' }));

    await screen.findByText(/ownership 已变更/);
    // 挂载 1 次 + 冲突后刷新 1 次。
    await waitFor(() => expect(getBotOwnership).toHaveBeenCalledTimes(2));
  });

  it('DELETE 管理者带 remaining_team_sources 时不视为移除', async () => {
    const user = userEvent.setup();
    revokeBotManager.mockResolvedValue(
      okEnvelope({ bot_id: BOT_ID, user_id: 'user-b', revoked: true, remaining_team_sources: ['team-a'] }),
    );
    render(<BotOwnershipTransferPanel mode="bot" botId={BOT_ID} accessRelation="owner" />);

    await user.click(await screen.findByRole('button', { name: '移除管理者 user-b' }));
    await screen.findByText(/团队来源/);
    // revoked=true 不代表管理者权限清空：条目保持，不由客户端本地移除。
    expect(screen.getByText('user-b')).toBeInTheDocument();
  });
});

describe('Human 视角：转交收发件', () => {
  it('接收人确认/拒绝收到的转交，接受 UI 明示 BCS-only 边界', async () => {
    const user = userEvent.setup();
    listBotOwnershipTransfers.mockImplementation((params: { direction?: string }) =>
      Promise.resolve(
        params?.direction === 'sent'
          ? receiptsEnvelope([])
          : receiptsEnvelope([receipt({ to_user_id: 'user-me', from_user_id: 'user-a' })]),
      ),
    );
    acceptBotOwnershipTransfer.mockResolvedValue(
      okEnvelope(receipt({ status: 'accepted', to_user_id: 'user-me' })),
    );
    render(<BotOwnershipTransferPanel mode="human" />);

    await screen.findByText('收到的转交');
    await screen.findByText(/Owned Bot/);
    expect(screen.getByText(/Owned Bot/)).toBeInTheDocument();
    expect(screen.getByText('来自 user-a')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '确认接受' }));

    await waitFor(() => expect(acceptBotOwnershipTransfer).toHaveBeenCalledWith('transfer-1'));
    await screen.findByText('已接受');
  });

  it('接收人拒绝收到的转交', async () => {
    const user = userEvent.setup();
    listBotOwnershipTransfers.mockImplementation((params: { direction?: string }) =>
      Promise.resolve(
        params?.direction === 'sent'
          ? receiptsEnvelope([])
          : receiptsEnvelope([receipt({ to_user_id: 'user-me' })]),
      ),
    );
    rejectBotOwnershipTransfer.mockResolvedValue(
      okEnvelope(receipt({ status: 'rejected' })),
    );
    render(<BotOwnershipTransferPanel mode="human" />);

    await screen.findByText(/Owned Bot/);
    await user.click(screen.getByRole('button', { name: '拒绝转交' }));
    await waitFor(() => expect(rejectBotOwnershipTransfer).toHaveBeenCalledWith('transfer-1'));
    await screen.findByText('已拒绝');
  });

  it('发起人取消发出的转交（仅一 pending）', async () => {
    const user = userEvent.setup();
    listBotOwnershipTransfers.mockImplementation((params: { direction?: string }) =>
      Promise.resolve(
        params?.direction === 'sent'
          ? receiptsEnvelope([receipt({ transfer_id: 'transfer-s1' })])
          : receiptsEnvelope([]),
      ),
    );
    cancelBotOwnershipTransfer.mockResolvedValue(
      okEnvelope(receipt({ transfer_id: 'transfer-s1', status: 'cancelled' })),
    );
    render(<BotOwnershipTransferPanel mode="human" />);

    await screen.findByText('发出的转交');
    await screen.findByText(/Owned Bot/);
    // 一个 Bot 只允许一个 pending：发件列表里的 pending 只出现一个取消入口。
    expect((await screen.findAllByRole('button', { name: '取消转交' }))).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: '取消转交' }));

    await waitFor(() => expect(cancelBotOwnershipTransfer).toHaveBeenCalledWith('transfer-s1'));
    await screen.findByText('已取消');
  });
});
