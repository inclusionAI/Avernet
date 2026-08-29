import {
  filterPublicBots,
  filterPublicGroups,
  mapBotProfileTransport,
  mapGroupMembersTransport,
  mapPublicGroupCatalogDto,
  parseSquareDeepLink,
} from '../src/domain/collaborationSquare/mapper';

const bots = [
  {
    id: 'b1',
    name: '产品协作助手',
    ownerName: '示例产品负责人',
    description: '需求分析与产品规划',
    capabilities: ['需求分析'],
    relationshipStatus: 'none' as const,
  },
  {
    id: 'b2',
    name: '研发助手',
    ownerName: '示例研发负责人',
    description: '代码审查',
    capabilities: ['代码审查'],
    relationshipStatus: 'friend' as const,
  },
];

describe('collaboration square model', () => {
  test('Bot 名称和智能搜索只使用公开字段', () => {
    expect(filterPublicBots(bots, ' 产品 ', 'name').map((item) => item.id)).toEqual(['b1']);
    expect(filterPublicBots(bots, '研发负责人', 'name').map((item) => item.id)).toEqual(['b2']);
    expect(filterPublicBots(bots, '代码', 'smart').map((item) => item.id)).toEqual(['b2']);
    expect(filterPublicBots(bots, '', 'smart')).toHaveLength(2);
  });

  test('群搜索忽略首尾空格且不区分大小写', () => {
    const groups = [
      { id: 'g1', name: 'Agent Builders' },
      { id: 'g2', name: '产品共创群' },
    ];
    expect(filterPublicGroups(groups, ' agent ').map((item) => item.id)).toEqual(['g1']);
  });

  test('Mapper 只保留公开画像和成员字段', () => {
    const profile = mapBotProfileTransport({
      bot_id: 'b1',
      bot_name: '助手',
      owner_name: 'Owner',
      engine_name: 'OpenClaw',
      description: '公开描述',
      capabilities: [{ capability_id: 'c1', display_name: '需求分析' }],
      secret_token: 'never-render',
    });
    expect(profile).toEqual(expect.objectContaining({ id: 'b1', engine: 'OpenClaw' }));
    expect(JSON.stringify(profile)).not.toContain('secret_token');

    const members = mapGroupMembersTransport([
      {
        member_id: 'u1',
        display_name: '示例用户',
        member_type: 'human',
        group_role: '参与者',
        online_status: 'online',
        can_manage: true,
      },
      { member_id: 'x1', display_name: '未知对象', member_type: 'service', group_role: '参与者', instance_env: 'prod' },
    ]);
    expect(members).toEqual([{ id: 'u1', displayName: '示例用户', type: 'human', role: '参与者' }]);
    expect(JSON.stringify(members)).not.toContain('online');
    expect(JSON.stringify(members)).not.toContain('prod');
  });

  test('公开群目录 Mapper 丢弃非公开、非 normal 和无 ID 的条目', () => {
    expect(mapPublicGroupCatalogDto({ group_id: '', visibility: 'public', kind: 'normal' })).toBeNull();
    expect(mapPublicGroupCatalogDto({ group_id: 'private', visibility: 'private', kind: 'normal' })).toBeNull();
    expect(mapPublicGroupCatalogDto({ group_id: 'dm', visibility: 'public', kind: 'dm' })).toBeNull();
  });

  test('公开群目录 Mapper 只从已确认的人类和 Bot actor 推导群主信息', () => {
    const group = mapPublicGroupCatalogDto({
      group_id: 'group-1',
      name: '公开协作群',
      visibility: 'public',
      kind: 'normal',
      status: 'active',
      originator_actor_id: 'service-1',
      driver_bot_uuid: 'service-2',
      participants: [
        { actor_id: 'service-1', actor_kind: 'service', name: '未知发起者' },
        { actor_id: 'service-2', actor_kind: 'service', name: '未知主理者' },
      ],
      collaboration: { strategy: 'manager_worker' },
    });

    expect(group).toEqual(
      expect.objectContaining({
        ownerBotName: '未公开',
        ownerUserName: '未公开',
        typeLabel: '主从协作群',
        memberListVisibility: 'count_only',
        canCreateSession: true,
      }),
    );
    expect(JSON.stringify(group)).not.toContain('未知发起者');
    expect(JSON.stringify(group)).not.toContain('未知主理者');
  });

  test('深链只接受当前资源类型和非空 id', () => {
    expect(parseSquareDeepLink('?resource=bot&id=b1', 'bot')).toEqual({ resource: 'bot', id: 'b1' });
    expect(parseSquareDeepLink('?resource=group&id=g1', 'bot')).toBeNull();
    expect(parseSquareDeepLink('?resource=bot&id=', 'bot')).toBeNull();
  });
});
