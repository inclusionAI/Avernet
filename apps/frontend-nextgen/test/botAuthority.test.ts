/**
 * botAuthority（Bot ownership/manager 契约消费）服务层测试：
 * - mine 独立 DTO 的 access_relation 直通 IdentityView（不 default、不从 created_by 推断）；
 * - botAuthorityService 保留角色与 sources 结果（revocation 的 remaining_team_sources 原样保留）；
 * - mine Bot 行缺 access_relation = 合同错误，不补默认 owner；
 * - 403 → 刷新并清理失权选中视角；
 * - ownership transfer 幂等键：提交失败重试同 key，退回错误（409 ownership_changed）即刷新；
 * - Human 共同建群传 Human originator（合成「我」不伪造 originator）、正确 private Group。
 *
 * fixture 特意让 created_by 与当前角色不一致：access_relation 只能来自 mine 行本身。
 */
import * as ownedBotController from '@/services/backendApi/bots/botController';
import * as botController from '@/services/backendApi/collaboration/collaborationBotController';
import * as ownershipController from '@/services/backendApi/collaboration/botOwnershipController';
import { buildCreateGroupBody } from '@/services/workspace/groupCreateRequest';
import { botAuthorityService, AUTHORITY_CONTRACT_ERROR } from '@/services/workspace/botAuthorityService';
import { identityService } from '@/services/workspace/identityService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { describe, expect, it, jest, beforeEach } from '@jest/globals';

// identityService 经 testUser→supportProvider transitive 加载 `@tc-chat/adapters`(ESM)，
// node 环境直接 load 会 SyntaxError，此处 stub(与 identityService.test.ts 同模式)。
jest.mock('@tc-chat/adapters', () => ({}));
jest.mock('@/services/backendApi/collaboration/collaborationBotController');
jest.mock('@/services/backendApi/bots/botController');
jest.mock('@/services/backendApi/collaboration/botOwnershipController');

const listMyBots = (botController as unknown as { listMyBots: jest.Mock<any> }).listMyBots;
const listBots = (ownedBotController as unknown as { listBots: jest.Mock<any> }).listBots;
const getBotOwnership = (ownershipController as unknown as { getBotOwnership: jest.Mock<any> })
  .getBotOwnership;
const createBotOwnershipTransfer = (ownershipController as unknown as {
  createBotOwnershipTransfer: jest.Mock<any>;
}).createBotOwnershipTransfer;
const revokeBotManager = (ownershipController as unknown as { revokeBotManager: jest.Mock<any> })
  .revokeBotManager;

function mineEnvelope(items: unknown[]) {
  return { code: 20000, message: '', request_id: 'r', data: { items, total: items.length, offset: 0, limit: 20 } };
}

/** created_by 与当前角色不一致的 mine 行：标记必须来自 access_relation 本身。 */
function authorityMineEnvelope() {
  return mineEnvelope([
    {
      kind: 'bot',
      bot_id: 'bot-owned',
      name: 'Owned Bot',
      access_relation: 'owner',
      // 过去创建者非当前 owner：owner 身份来自当前 authority 边，不是 created_by。
      created_by: 'formal-creator-1',
      status: 'online',
    },
    {
      kind: 'bot',
      bot_id: 'bot-managed',
      name: 'Managed Bot',
      access_relation: 'manager',
      // manager 来源 Bot 甚至可能由他人创建：created_by 与角色毫无推断关系。
      created_by: 'some-other-user',
      status: 'online',
    },
  ]);
}

beforeEach(() => {
  listMyBots.mockReset();
  listBots.mockReset();
  getBotOwnership.mockReset();
  createBotOwnershipTransfer.mockReset();
  revokeBotManager.mockReset();
  listBots.mockResolvedValue({ code: 200000, message: '', request_id: 'r-engine', data: { items: [] } });
  useWorkspaceStore.getState().setIdentities([], null);
});

describe('mine access_relation → 视角身份', () => {
  it('owner/manager 身份直通角色标签，不从 created_by 推断', async () => {
    listMyBots.mockResolvedValue(authorityMineEnvelope());
    const res = await identityService.loadIdentities();
    if (!res.ok) throw new Error('loadIdentities 失败');
    const ownedIdentity = res.data.identities.find((i) => i.id === 'bot-owned');
    const managedIdentity = res.data.identities.find((i) => i.id === 'bot-managed');
    expect(ownedIdentity?.accessRelation).toBe('owner');
    expect(managedIdentity?.accessRelation).toBe('manager');
    // 身份切换纳入 manager 来源的 Bot（视角列表包含 manager 行）。
    expect(managedIdentity?.id).toBe('bot-managed');
  });

  it('mine Bot 行缺 access_relation 按合同错误失败，不补默认 owner', async () => {
    listMyBots.mockResolvedValue(
      mineEnvelope([{ kind: 'bot', bot_id: 'bot-legacy', status: 'online', created_by: 'formal-creator-1' }]),
    );
    const res = await botAuthorityService.loadAuthorities();
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error.code).toBe(AUTHORITY_CONTRACT_ERROR);
  });
});

describe('botAuthorityService 保留角色与 sources 结果', () => {
  it('loadAuthorities 返回 owner/manager 角色并透传 created_by（仅排障用）', async () => {
    listMyBots.mockResolvedValue(authorityMineEnvelope());
    const res = await botAuthorityService.loadAuthorities();
    if (!res.ok) throw new Error('loadAuthorities 失败');
    expect(res.data.map((a) => [a.botId, a.accessRelation])).toEqual([
      ['bot-owned', 'owner'],
      ['bot-managed', 'manager'],
    ]);
    expect(res.data.find((a) => a.botId === 'bot-managed')?.createdBy).toBe('some-other-user');
  });

  it('revokeManager 原样保留 remaining_team_sources，身份列表不被移除', async () => {
    listMyBots.mockResolvedValue(authorityMineEnvelope());
    const identities = await identityService.loadIdentities();
    if (!identities.ok) throw new Error('identities 失败');
    useWorkspaceStore.getState().setIdentities(identities.data.identities, 'bot-managed');

    revokeBotManager.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { bot_id: 'bot-owned', user_id: 'user-b', revoked: true, remaining_team_sources: ['team-a'] },
    });
    const result = await botAuthorityService.revokeManager('bot-owned', 'user-b');
    if (!result.ok) throw new Error('revokeManager 失败');
    const deleteResult = result.data;
    // 直接 DELETE 不触碰 team/* 来源；仍保留团队来源＝管理者权限未失效。
    expect(deleteResult.remaining_team_sources).toEqual(['team-a']);

    // team 来源仍在 ⇒ 服务不得把该 Bot 从身份列表移除，不得清空选中视角。
    const store = useWorkspaceStore.getState();
    expect(store.identities.some((i) => i.id === 'bot-managed')).toBe(true);
    expect(store.activeIdentityId).toBe('bot-managed');
  });

  it('ownership 读 403 时刷新身份并清理失权选中视角', async () => {
    listMyBots.mockResolvedValue(authorityMineEnvelope());
    const identities = await identityService.loadIdentities();
    if (!identities.ok) throw new Error('identities 失败');
    useWorkspaceStore.getState().setIdentities(identities.data.identities, 'bot-managed');

    getBotOwnership.mockRejectedValue({ status: 403, data: { data: { error_code: 'forbidden' } } });
    // 刷新后 mine 只剩 human 行：manager 权限已被撤 ⇒ bot-managed 不再出现在视角列表。
    listMyBots.mockResolvedValue(
      mineEnvelope([{ kind: 'human', bot_id: 'human-1', name: '示例用户', status: 'online' }]),
    );

    const res = await botAuthorityService.getOwnership('bot-managed');
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error.code).toBe('OWNERSHIP_FORBIDDEN');

    const store = useWorkspaceStore.getState();
    expect(store.identities.some((i) => i.id === 'bot-managed')).toBe(false);
    expect(store.activeIdentityId).not.toBe('bot-managed');
  });
});

describe('ownership transfer 提交幂等键', () => {
  it('提交成功响应丢失后用同 client_request_id 重试；成功后重新发起换新 key', async () => {
    const receipt = {
      transfer_id: 'transfer-1',
      bot_id: 'bot-owned',
      from_user_id: 'user-a',
      to_user_id: 'user-b',
      status: 'pending',
      expected_owner_version: 3,
      expires_at: 1,
      bot_name_snapshot: 'Owned Bot',
      gmt_create: 1,
      gmt_modified: 1,
    };
    createBotOwnershipTransfer
      .mockRejectedValueOnce(new Error('network dropped'))
      .mockResolvedValue({ code: 20100, message: '', request_id: 'r', data: receipt });

    const first = await botAuthorityService.createOwnershipTransfer('bot-owned', 'user-b', 3);
    expect(first.ok).toBe(false);
    const second = await botAuthorityService.createOwnershipTransfer('bot-owned', 'user-b', 3);
    if (!second.ok) throw new Error('重试应成功拿到 receipt');
    expect(second.data.transferId).toBe('transfer-1');

    expect(createBotOwnershipTransfer).toHaveBeenCalledTimes(2);
    const firstKey = createBotOwnershipTransfer.mock.calls[0][1].client_request_id;
    const retryKey = createBotOwnershipTransfer.mock.calls[1][1].client_request_id;
    // 重试必须用同一把幂等 key（服务端 201-once / 200-replay 语义靠它）。
    expect(retryKey).toBe(firstKey);
    // receipt（历史记录，永不命名 current_owner）原样映射回视图。
    expect(second.data).toEqual({
      transferId: 'transfer-1',
      botId: 'bot-owned',
      fromUserId: 'user-a',
      toUserId: 'user-b',
      status: 'pending',
      expectedOwnerVersion: 3,
      expiresAt: 1,
      botNameSnapshot: 'Owned Bot',
    });

    // 成功提交已返回 receipt ⇒ 下一次全新发起必须换新 key，不得重放旧 receipt。
    await botAuthorityService.createOwnershipTransfer('bot-owned', 'user-c', 3);
    const freshKey = createBotOwnershipTransfer.mock.calls[2][1].client_request_id;
    expect(freshKey).not.toBe(firstKey);
  });

  it('409 ownership_changed 归类为 OWNERSHIP_CHANGED（由消费方重读快照）', async () => {
    createBotOwnershipTransfer.mockRejectedValue({
      status: 409,
      data: { data: { error_code: 'ownership_changed' } },
    });
    const res = await botAuthorityService.createOwnershipTransfer('bot-owned', 'user-b', 3);
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error.code).toBe('OWNERSHIP_CHANGED');
  });
});

describe('Human 共同建群 originator/受控 Bot/private Group', () => {
  const baseInput = {
    name: '协作群',
    strategy: 'chat' as const,
    driverBotUuid: 'bot-owned',
    participants: [{ actor_id: 'bot-owned' }, { actor_id: 'bot-managed' }],
  };

  it('Human 视角用 Human originator；受控 Bots（含 manager 来源）原样进参与者', () => {
    const body = buildCreateGroupBody({ ...baseInput, originator: 'human_900004' });
    expect(body.originator).toBe('human_900004');
    expect(body.participants.map((p) => p.actor_id)).toEqual(['bot-owned', 'bot-managed']);
    // 建群 body 不携带公私可见性：BCS 默认建 private Group，前端不追加 public patch。
    expect('visibility' in body).toBe(false);
  });

  it('合成「我」不伪造 originator（省略让后端回落 Human Principal）', () => {
    const body = buildCreateGroupBody({ ...baseInput, originator: 'me' });
    expect(body.originator).toBeUndefined();
  });
});
