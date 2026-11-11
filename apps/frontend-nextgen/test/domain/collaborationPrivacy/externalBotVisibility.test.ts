import type { CollaborationBot } from '@/domain/collaborationPrivacy/types';
import {
  applyExternalViewScopeToBot,
  buildExternalViewScopeFriendExt,
  normalizeExternalViewScopeConfig,
  readExternalViewScopeNames,
} from '@/domain/collaborationPrivacy/externalBotVisibility';

describe('external bot visibility domain', () => {
  test('private 或缺失 visibility 一律映射为不可见', () => {
    expect(normalizeExternalViewScopeConfig('private', { view_scope_agent_friend_deps: ['蚂蚁集团-安全部'] })).toEqual(
      { scope: 'none', organizationPaths: [] },
    );
    expect(normalizeExternalViewScopeConfig(undefined, {})).toEqual({ scope: 'none', organizationPaths: [] });
  });

  test('public 即使保留旧行的部门范围也展示为全部可见', () => {
    // 外部 Bot 切回 public 只提交 visibility；后端可能保留陈旧的 view_scope_agent_friend_deps，展示必须忽略。
    expect(
      normalizeExternalViewScopeConfig('public', { view_scope_agent_friend_deps: ['蚂蚁集团-CRO线-大安全技术部'] }),
    ).toEqual({ scope: 'all', organizationPaths: [] });
  });

  test('protected + 名称字符串数组映射为限定组织可申请，且名称原样作为回显路径', () => {
    expect(
      normalizeExternalViewScopeConfig('protected', {
        view_scope_agent_friend_deps: ['蚂蚁集团-CRO线-大安全技术部', '蚂蚁集团-安全部'],
      }),
    ).toEqual({
      scope: 'restricted',
      organizationPaths: [['蚂蚁集团-CRO线-大安全技术部'], ['蚂蚁集团-安全部']],
    });
  });

  test('protected + 旧对象形态（deptNo/deptName）按 deptName 名称回显，兼容历史写入', () => {
    expect(
      normalizeExternalViewScopeConfig('protected', {
        view_scope_agent_friend_deps: [{ deptNo: 'A4195', deptName: '蚂蚁集团-CRO线-大安全技术部' }],
      }),
    ).toEqual({
      scope: 'restricted',
      organizationPaths: [['蚂蚁集团-CRO线-大安全技术部']],
    });
  });

  test('protected 且无部门范围时展示为全部可见', () => {
    expect(normalizeExternalViewScopeConfig('protected', {})).toEqual({ scope: 'all', organizationPaths: [] });
    expect(normalizeExternalViewScopeConfig('protected', undefined)).toEqual({ scope: 'all', organizationPaths: [] });
  });

  test('readExternalViewScopeNames 去空白、按序去重、兼容对象名称字段', () => {
    expect(
      readExternalViewScopeNames({
        view_scope_agent_friend_deps: [' 蚂蚁集团-安全部 ', '蚂蚁集团-安全部', '', { deptName: '蚂蚁集团-平台部' }, 42],
      }),
    ).toEqual(['蚂蚁集团-安全部', '蚂蚁集团-平台部']);
    expect(readExternalViewScopeNames({})).toEqual([]);
    expect(readExternalViewScopeNames(undefined)).toEqual([]);
  });

  test('buildExternalViewScopeFriendExt 整体替换语义：保留全部未知 friend_ext 键，只替换部门范围键', () => {
    const currentFriendExt = {
      view_scope_user_friend_deps: ['U1'],
      no_check_scope_friend_deps: ['D9'],
      public_agent_approval: { status: 'PENDING', puid: 'PU-9', approval_url: 'https://example.com/x' },
      custom_flags: { nested: { keep: true } },
      view_scope_agent_friend_deps: ['旧范围'],
    };
    const patched = buildExternalViewScopeFriendExt(currentFriendExt, ['蚂蚁集团-CRO线-大安全技术部']);
    expect(patched).toEqual({
      view_scope_user_friend_deps: ['U1'],
      no_check_scope_friend_deps: ['D9'],
      public_agent_approval: { status: 'PENDING', puid: 'PU-9', approval_url: 'https://example.com/x' },
      custom_flags: { nested: { keep: true } },
      view_scope_agent_friend_deps: ['蚂蚁集团-CRO线-大安全技术部'],
    });
    // 不修改调用方持有的原对象。
    expect(currentFriendExt.view_scope_agent_friend_deps).toEqual(['旧范围']);
  });

  test('friend_ext 缺失或非对象时从空对象开始合并', () => {
    expect(buildExternalViewScopeFriendExt(undefined, ['蚂蚁集团-安全部'])).toEqual({
      view_scope_agent_friend_deps: ['蚂蚁集团-安全部'],
    });
    expect(buildExternalViewScopeFriendExt('not-a-record', [])).toEqual({ view_scope_agent_friend_deps: [] });
  });

  test('applyExternalViewScopeToBot 只覆写对 Bot 可见性并清空审批态', () => {
    const bot: CollaborationBot = {
      id: 'external-1',
      name: '外部助手',
      engine: 'OpenClaw',
      joinedBcn: true,
      collaborationStatus: 'online',
      profilePublic: false,
      taskClaimingEnabled: false,
      dreamModelEnabled: false,
      publication: {
        user: { scope: 'none', organizationPaths: [] },
        bot: { scope: 'all', organizationPaths: [] },
      },
      pendingPublications: {
        bot: { id: 'PU-1', audience: 'bot', target: { scope: 'all', organizationPaths: [] }, submittedAt: '' },
      },
      friendApproval: { mode: 'all', exemptOrganizationPaths: [] },
    };
    const next = applyExternalViewScopeToBot(bot, {
      visibility: 'protected',
      friend_ext: { view_scope_agent_friend_deps: ['蚂蚁集团-CRO线-大安全技术部'] },
    });
    expect(next.publication).toEqual({
      user: { scope: 'none', organizationPaths: [] },
      bot: { scope: 'restricted', organizationPaths: [['蚂蚁集团-CRO线-大安全技术部']] },
    });
    expect(next.pendingPublications).toEqual({});
    expect(bot.pendingPublications.bot).toBeDefined();
  });
});
