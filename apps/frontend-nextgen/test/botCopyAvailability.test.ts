/**
 * Bot 复制入口可见性门控测试(canCopyBot),按 Bot 类型分流:
 * - AC-4:非 openclaw 引擎不展示。
 * - 个人 Bot(非服务):仅 owner 可复制;非 owner 置灰「仅Bot Owner可复制」。
 * - 服务 Bot:
 *   - 无操作权限(actions 仅 view/chat)→ 置灰「无 Bot 操作权限,请先申请」。
 *   - owner + 无协作者 → 免锁放行(锁表短路)。
 *   - 有操作权限但需持锁:本人持锁放行;他人持锁置灰;无人持锁置灰「请先获取锁」。
 * - AC-5:lifecycle 阻断(deploying/failed/unknown)在通过前置门后给现有中文文案;前置门文案优先于 lifecycle。
 *
 * 对应需求 Dima 2026112300120878745 门 B v2(权限/锁分流)。
 */
import { describe, it, expect } from '@jest/globals';

import { canCopyBot } from '@/domain/botWorkshop';
import type { BotDomain, BotLifecycle, BotLock } from '@/domain/botWorkshop';

/** 构造满足 BotDomain 类型约束的最小测试 bot;canCopyBot 仅读 engine/serviceMode/ownerId/actions/hasCollaborators/lock/lifecycle。 */
function makeBot(overrides: Partial<BotDomain>): BotDomain {
  const base: BotDomain = {
    id: 'bot-1',
    entityKey: 'entity-1',
    name: 'Test Bot',
    spaceKind: 'team',
    ownership: 'team',
    deployment: 'cloud',
    serviceMode: 'non-service',
    canUpgradeToService: false,
    lifecycle: 'draft',
    runtime: {
      engine: 'openclaw',
      isAgentCodingBot: false,
      isDefaultBot: false,
      capabilityProfile: { canPublish: true, canEdit: true, canChat: true, canViewLogs: true },
      visibleInOpenCore: true,
    },
    completeness: 'complete',
    warnings: [],
    actions: [],
    disabledActions: {},
    ownerId: 'user-self',
  };
  return { ...base, ...overrides, runtime: { ...base.runtime, ...(overrides.runtime ?? {}) } };
}

describe('canCopyBot', () => {
  const selfUserId = 'user-self';

  describe('AC-4: 非 openclaw 引擎不展示', () => {
    it.each(['moltis', 'teclaw', 'hermes', 'claude_code'] as const)(
      '引擎 %s → visible:false',
      (engine) => {
        const bot = makeBot({ runtime: { ...makeBot({}).runtime, engine } });
        expect(canCopyBot(bot, selfUserId)).toEqual({ visible: false, enabled: false });
      },
    );
  });

  describe('个人 Bot(非服务):仅 owner 可复制', () => {
    it('owner + draft → 放行', () => {
      const bot = makeBot({ ownerId: selfUserId, serviceMode: 'non-service', lifecycle: 'draft' });
      expect(canCopyBot(bot, selfUserId)).toEqual({ visible: true, enabled: true });
    });

    it('非 owner → 置灰「仅Bot Owner可复制」(即便 actions 含 edit 也不放行)', () => {
      const bot = makeBot({
        ownerId: 'someone-else',
        serviceMode: 'non-service',
        actions: ['view', 'edit'],
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: '仅Bot Owner可复制',
      });
    });

    it('无 currentUserId 且非 owner → 置灰「仅Bot Owner可复制」', () => {
      const bot = makeBot({ ownerId: 'someone-else', serviceMode: 'non-service' });
      expect(canCopyBot(bot, undefined)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: '仅Bot Owner可复制',
      });
    });
  });

  describe('服务 Bot:操作权限门', () => {
    it('无操作权限(actions 仅 view/chat)→ 置灰「无 Bot 操作权限,请先申请」', () => {
      const bot = makeBot({
        ownerId: 'someone-else',
        serviceMode: 'service',
        actions: ['view', 'chat'],
        hasCollaborators: true,
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: '无 Bot 操作权限,请先申请',
      });
    });

    it('无操作权限 + lifecycle 阻断 → 权限文案优先于 lifecycle', () => {
      const bot = makeBot({
        ownerId: 'someone-else',
        serviceMode: 'service',
        actions: ['view'],
        hasCollaborators: true,
        lifecycle: 'deploying',
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: '无 Bot 操作权限,请先申请',
      });
    });
  });

  describe('服务 Bot:锁门', () => {
    it('owner + 无协作者 → 免锁放行(锁表短路)', () => {
      const bot = makeBot({
        ownerId: selfUserId,
        serviceMode: 'service',
        actions: ['view', 'edit'],
        hasCollaborators: false,
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({ visible: true, enabled: true });
    });

    it('owner + 有协作者 + 本人持锁 → 放行', () => {
      const lock: BotLock = { status: 'mine', holderUserId: selfUserId };
      const bot = makeBot({
        ownerId: selfUserId,
        serviceMode: 'service',
        actions: ['view', 'edit'],
        hasCollaborators: true,
        lock,
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({ visible: true, enabled: true });
    });

    it('非 owner 协作者 + 本人持锁 → 放行', () => {
      const lock: BotLock = { status: 'mine', holderUserId: selfUserId };
      const bot = makeBot({
        ownerId: 'someone-else',
        serviceMode: 'service',
        actions: ['view', 'edit'],
        hasCollaborators: true,
        lock,
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({ visible: true, enabled: true });
    });

    it('有操作权限 + 他人持锁 → 置灰「Bot 正被他人编辑,请稍后再试」', () => {
      const lock: BotLock = { status: 'other', holderUserId: 'someone-else' };
      const bot = makeBot({
        ownerId: selfUserId,
        serviceMode: 'service',
        actions: ['view', 'edit'],
        hasCollaborators: true,
        lock,
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: 'Bot 正被他人编辑,请稍后再试',
      });
    });

    it('有操作权限 + 无人持锁(lock 缺失)→ 置灰「请先获取锁」', () => {
      const bot = makeBot({
        ownerId: selfUserId,
        serviceMode: 'service',
        actions: ['view', 'edit'],
        hasCollaborators: true,
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: '请先获取锁',
      });
    });

    it('有操作权限 + 无人持锁 + lifecycle 阻断 → 锁文案优先于 lifecycle', () => {
      const bot = makeBot({
        ownerId: selfUserId,
        serviceMode: 'service',
        actions: ['view', 'edit'],
        hasCollaborators: true,
        lifecycle: 'deploying',
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: '请先获取锁',
      });
    });
  });

  describe('AC-5: lifecycle 阻断保持现有中文文案', () => {
    it.each([
      ['deploying', 'Bot 部署中,无法复制'],
      ['failed', 'Bot 创建失败,无法复制'],
      ['unknown', 'Bot 状态未知,无法复制'],
    ] as Array<[BotLifecycle, string]>)('个人 bot owner + %s → 置灰且文案不变', (lifecycle, reason) => {
      const bot = makeBot({ ownerId: selfUserId, serviceMode: 'non-service', lifecycle });
      expect(canCopyBot(bot, selfUserId)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: reason,
      });
    });

    it('服务 bot owner + 无协作者 + %s → lifecycle 文案', () => {
      const bot = makeBot({
        ownerId: selfUserId,
        serviceMode: 'service',
        actions: ['view', 'edit'],
        hasCollaborators: false,
        lifecycle: 'deploying',
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: 'Bot 部署中,无法复制',
      });
    });

    it('服务 bot owner + 有协作者 + 本人持锁 + deploying → lifecycle 文案', () => {
      const lock: BotLock = { status: 'mine', holderUserId: selfUserId };
      const bot = makeBot({
        ownerId: selfUserId,
        serviceMode: 'service',
        actions: ['view', 'edit'],
        hasCollaborators: true,
        lock,
        lifecycle: 'deploying',
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: 'Bot 部署中,无法复制',
      });
    });
  });
});
