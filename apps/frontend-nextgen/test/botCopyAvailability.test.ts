/**
 * Bot 复制入口可见性门控测试(canCopyBot):
 * - AC-4:非 openclaw 引擎不展示。
 * - AC-1/2:owner 或持 edit 协作权限 → 展示且可点击。
 * - AC-3:无权限 → 展示但置灰,tooltip=bot.disabledActions.edit(与后端 disabled_actions
 *   直给文案「Bot editor permission required」一致;缺失走兜底「当前不可复制」)。
 * - AC-5:lifecycle 阻断(deploying/failed/unknown)在有权限时保持现有中文文案。
 *
 * 对应需求 Dima 2026112300120878745 门 B v1。
 */
import { describe, it, expect } from '@jest/globals';

import { canCopyBot } from '@/domain/botWorkshop';
import type { BotDomain, BotLifecycle } from '@/domain/botWorkshop';

/** 构造满足 BotDomain 类型约束的最小测试 bot;canCopyBot 仅读 engine/ownerId/actions/lifecycle/disabledActions。 */
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

  describe('AC-1: owner 可点击', () => {
    it('openclaw + owner + draft → visible:true enabled:true', () => {
      const bot = makeBot({ ownerId: selfUserId, actions: [], lifecycle: 'draft' });
      expect(canCopyBot(bot, selfUserId)).toEqual({ visible: true, enabled: true });
    });
  });

  describe('AC-2: 持 edit 协作权限可点击', () => {
    it('openclaw + 非 owner + actions 含 edit → visible:true enabled:true', () => {
      const bot = makeBot({
        ownerId: 'someone-else',
        actions: ['view', 'edit'],
        lifecycle: 'running',
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({ visible: true, enabled: true });
    });
  });

  describe('AC-3: 无权限展示置灰 + edit 文案', () => {
    it('openclaw + 非 owner + 无 edit + 后端下发 disabledActions.edit → 置灰且文案对齐', () => {
      const bot = makeBot({
        ownerId: 'someone-else',
        actions: ['view', 'chat'],
        disabledActions: { edit: 'Bot editor permission required' },
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: 'Bot editor permission required',
      });
    });

    it('openclaw + 无权限 + disabledActions 缺 edit key → 走兜底文案', () => {
      const bot = makeBot({ ownerId: 'someone-else', actions: ['view'], disabledActions: {} });
      expect(canCopyBot(bot, selfUserId)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: '当前不可复制',
      });
    });
  });

  describe('AC-5: lifecycle 阻断保持现有中文文案', () => {
    it.each([
      ['deploying', 'Bot 部署中,无法复制'],
      ['failed', 'Bot 创建失败,无法复制'],
      ['unknown', 'Bot 状态未知,无法复制'],
    ] as Array<[BotLifecycle, string]>)('owner + %s → 置灰且文案不变', (lifecycle, reason) => {
      const bot = makeBot({ ownerId: selfUserId, lifecycle });
      expect(canCopyBot(bot, selfUserId)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: reason,
      });
    });

    it('无权限 + lifecycle 阻断 → 权限文案优先于 lifecycle 文案', () => {
      // 无权限时即使 lifecycle 也阻断,仍给权限文案(lifecycle 仅在有权限后才裁决)。
      const bot = makeBot({
        ownerId: 'someone-else',
        actions: ['view'],
        lifecycle: 'deploying',
        disabledActions: { edit: 'Bot editor permission required' },
      });
      expect(canCopyBot(bot, selfUserId)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: 'Bot editor permission required',
      });
    });
  });

  describe('边界', () => {
    it('无 currentUserId → 视为无权限,展示置灰', () => {
      const bot = makeBot({
        ownerId: 'someone-else',
        actions: [],
        disabledActions: { edit: 'Bot editor permission required' },
      });
      expect(canCopyBot(bot, undefined)).toEqual({
        visible: true,
        enabled: false,
        disabledReason: 'Bot editor permission required',
      });
    });
  });
});
