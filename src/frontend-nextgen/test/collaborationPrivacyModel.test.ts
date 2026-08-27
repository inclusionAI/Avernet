import {
  friendApprovalConfigsEqual,
  normalizeOrganizationPaths,
  normalizePublicConfig,
  publicConfigsEqual,
  validateFriendApproval,
} from '../src/domain/collaborationPrivacy/policies';
import { mapOverviewTransport } from '../src/domain/collaborationPrivacy/mapper';

describe('collaboration privacy domain policies', () => {
  test('组织路径会去空、去重并稳定排序', () => {
    expect(
      normalizeOrganizationPaths([
        [' 蚂蚁集团 ', '平台技术事业群'],
        ['蚂蚁集团', '平台技术事业群'],
        ['', ''],
        ['蚂蚁集团', '数字科技事业群'],
      ]),
    ).toEqual([
      ['蚂蚁集团', '平台技术事业群'],
      ['蚂蚁集团', '数字科技事业群'],
    ]);
  });

  test('非 restricted 配置清空路径且比较不依赖输入顺序', () => {
    expect(normalizePublicConfig({ scope: 'all', organizationPaths: [['不应保留']] })).toEqual({
      scope: 'all',
      organizationPaths: [],
    });
    expect(
      publicConfigsEqual(
        { scope: 'restricted', organizationPaths: [['B'], ['A']] },
        { scope: 'restricted', organizationPaths: [['A'], ['B'], ['A']] },
      ),
    ).toBe(true);
  });

  test('partial_exempt 至少需要一个组织范围', () => {
    expect(() => validateFriendApproval({ mode: 'partial_exempt', exemptOrganizationPaths: [] })).toThrow(
      '至少选择一个免审批组织范围',
    );
  });

  test('好友审批配置比较会忽略路径顺序和非 partial_exempt 的冗余路径', () => {
    expect(friendApprovalConfigsEqual(
      { mode: 'partial_exempt', exemptOrganizationPaths: [['B'], ['A']] },
      { mode: 'partial_exempt', exemptOrganizationPaths: [['A'], ['B'], ['A']] },
    )).toBe(true);
    expect(friendApprovalConfigsEqual(
      { mode: 'all', exemptOrganizationPaths: [['不应保留']] },
      { mode: 'all', exemptOrganizationPaths: [] },
    )).toBe(true);
  });

  test('Mapper 对未知枚举使用安全值并隔离 transport shape', () => {
    const overview = mapOverviewTransport({
      current_user: { display_name: '示例用户', employee_no: 'SAMPLE-001', department_path: ['示例集团'] },
      organization_options: [['示例集团', '平台团队']],
      bots: [
        {
          bot_id: 'bot-1',
          bot_name: '示例 Bot',
          engine_name: 'OpenClaw',
          joined_bcn: true,
          collaboration_status: 'unexpected',
          profile_public: true,
          task_claiming_enabled: false,
          dream_model_enabled: false,
          publication: {
            user: { scope: 'restricted', organization_paths: [['示例集团', '平台团队']] },
            bot: { scope: 'none', organization_paths: [['should-clear']] },
          },
          friend_approval: { mode: 'all', exempt_organization_paths: [] },
          pending_publications: {},
        },
      ],
    });

    expect(overview.bots[0].collaborationStatus).toBe('offline');
    expect(overview.bots[0].publication.bot.organizationPaths).toEqual([]);
    expect(overview.currentUser.employeeNumber).toBe('SAMPLE-001');
    expect(overview.bots[0]).not.toHaveProperty('bot_id');
  });
});
