import {
  filterPublicBots,
  filterPublicGroups,
  mapBotProfileTransport,
  mapCollaborationBotDto,
  mapGroupMembersTransport,
  mapPublicBotCatalogDto,
  mapPublicGroupCatalogDto,
  parseSquareDeepLink,
  resolveFriendRequestBotId,
} from '../src/domain/collaborationSquare/mapper';
import {
  TASK_STATUS_CONFIG,
  getPublicTaskStatusPresentation,
  resolvePublicBotPrimaryAction,
  type PlazaTaskStatus,
  type SquareResource,
} from '../src/domain/collaborationSquare/types';

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

  test('通用协作 Bot 与画像 Mapper 优先保留后端 Bot UUID，缺失时回退 Bot ID', () => {
    expect(mapCollaborationBotDto({ bot_id: '20260825_mbu0ey8f', bot_uuid: '20260825_mbu0ey8f:447147' })).toEqual(
      expect.objectContaining({ id: '20260825_mbu0ey8f:447147' }),
    );
    expect(
      mapBotProfileTransport({ bot_id: 'default', bot_uuid: 'default:447147', bot_name: '助手', owner_name: 'Owner' }),
    ).toEqual(expect.objectContaining({ id: 'default:447147' }));
    expect(mapBotProfileTransport({ bot_id: 'default', bot_name: '助手', owner_name: 'Owner' }).id).toBe('default');
  });

  test('Bot Catalog 使用 bot_uuid 作为页面与操作 canonical id，并消费 Search 返回的 is_friend', () => {
    expect(
      resolveFriendRequestBotId({ bot_id: 'default', bot_uuid: '20260825_mbu0ey8f:447147', entity_id: '366656' }),
    ).toBe('20260825_mbu0ey8f:447147');
    expect(resolveFriendRequestBotId({ bot_id: 'default', entity_id: '366656' })).toBe('default:366656');
    expect(resolveFriendRequestBotId({ bot_id: 'default' })).toBe('default');
    expect(
      mapPublicBotCatalogDto({
        bot_id: 'default',
        bot_uuid: '20260825_mbu0ey8f:447147',
        entity_id: '366656',
        is_friend: true,
      }),
    ).toEqual(
      expect.objectContaining({
        id: '20260825_mbu0ey8f:447147',
        relationshipStatus: 'friend',
      }),
    );

    expect(
      mapPublicBotCatalogDto({ bot_id: 'default', bot_uuid: '20260825_mbu0ey8f:447147', is_friend: false }),
    ).toEqual(expect.objectContaining({ relationshipStatus: 'none' }));

    // 智能搜索 Discovery 的 recommendation.short_profile 映射为卡片 shortProfile。
    expect(
      mapPublicBotCatalogDto({
        bot_id: 'default',
        bot_uuid: '20260825_mbu0ey8f:447147',
        recommendation: { short_profile: '用于测试目的的专用 Bot' },
      }),
    ).toEqual(expect.objectContaining({ shortProfile: '用于测试目的的专用 Bot' }));
    expect(
      mapPublicBotCatalogDto({ bot_id: 'default', bot_uuid: '20260825_mbu0ey8f:447147' })?.shortProfile,
    ).toBeUndefined();

    expect(
      mapPublicBotCatalogDto({
        bot_id: '20260410_kt9ermvn',
        entity_id: '431368',
        name: '公开 Bot',
        owner_name: 'Owner',
      }),
    ).toEqual(
      expect.objectContaining({
        id: '20260410_kt9ermvn',
        friendRequestBotId: '20260410_kt9ermvn:431368',
      }),
    );

    expect(
      mapPublicBotCatalogDto(
        {
          bot_id: 'owned-bot',
          entity_id: '151220',
          name: '我的公开 Bot',
          owner_name: '当前用户',
          is_friend: false,
        },
        ' 151220 ',
      ),
    ).toEqual(
      expect.objectContaining({
        id: 'owned-bot',
        relationshipStatus: 'none',
        isOwnedByLoggedInUser: true,
      }),
    );
    const otherBot = mapPublicBotCatalogDto({ bot_id: 'other-bot', entity_id: '151221', is_friend: false }, '151220');
    expect(otherBot).toEqual(expect.objectContaining({ relationshipStatus: 'none' }));
    expect(otherBot).not.toHaveProperty('isOwnedByLoggedInUser');
  });

  test('公开 Bot 主操作按当前 actor、关系和 self-target 决定', () => {
    const human = { type: 'human' as const, id: '327325' };
    const botActor = { type: 'bot' as const, id: 'bot-viewer' };

    expect(
      resolvePublicBotPrimaryAction({
        activeActor: human,
        targetActorId: 'bot-target',
        relationshipStatus: 'friend',
        isOwnedByLoggedInUser: false,
      }),
    ).toBe('open_human_bot_conversation');
    expect(
      resolvePublicBotPrimaryAction({
        activeActor: botActor,
        targetActorId: 'bot-target',
        relationshipStatus: 'friend',
        isOwnedByLoggedInUser: false,
      }),
    ).toBe('friendship_established');
    expect(
      resolvePublicBotPrimaryAction({
        activeActor: botActor,
        targetActorId: 'bot-target',
        relationshipStatus: 'none',
        isOwnedByLoggedInUser: true,
      }),
    ).toBe('request_friendship');
    expect(
      resolvePublicBotPrimaryAction({
        activeActor: botActor,
        targetActorId: 'bot-viewer',
        relationshipStatus: 'none',
        isOwnedByLoggedInUser: true,
      }),
    ).toBe('self_target');
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
        typeLabel: '任务协作',
        memberListVisibility: 'count_only',
        canCreateSession: true,
      }),
    );
    expect(JSON.stringify(group)).not.toContain('未知发起者');
    expect(JSON.stringify(group)).not.toContain('未知主理者');
  });

  test('typeLabel 从顶层 strategy 字段映射：chat→自由聊天 / manager_worker→任务协作 / state_machine→自定义协同', () => {
    const base = {
      group_id: 'group-1',
      name: '公开协作群',
      visibility: 'public',
      kind: 'normal',
      status: 'active',
    };
    expect(mapPublicGroupCatalogDto({ ...base, strategy: 'chat' })?.typeLabel).toBe('自由聊天');
    expect(mapPublicGroupCatalogDto({ ...base, strategy: 'manager_worker' })?.typeLabel).toBe('任务协作');
    expect(mapPublicGroupCatalogDto({ ...base, strategy: 'state_machine' })?.typeLabel).toBe('自定义协同');
    // 缺省 strategy → 默认自由聊天
    expect(mapPublicGroupCatalogDto({ ...base })?.typeLabel).toBe('自由聊天');
    // 顶层 strategy 优先于嵌套 collaboration.strategy
    expect(
      mapPublicGroupCatalogDto({ ...base, strategy: 'state_machine', collaboration: { strategy: 'chat' } })?.typeLabel,
    ).toBe('自定义协同');
  });

  test('深链只接受当前资源类型和非空 id，并解析 Bot 名称搜索提示', () => {
    expect(parseSquareDeepLink('?resource=bot&id=b1&name=%E9%A1%B9%E7%9B%AE%E5%8A%A9%E6%89%8B', 'bot')).toEqual({
      resource: 'bot',
      id: 'b1',
      searchHint: '项目助手',
    });
    expect(parseSquareDeepLink('?resource=bot&id=b1', 'bot')).toEqual({ resource: 'bot', id: 'b1' });
    expect(parseSquareDeepLink('?resource=group&id=g1', 'bot')).toBeNull();
    expect(parseSquareDeepLink('?resource=bot&id=', 'bot')).toBeNull();
  });
});

describe('collaboration square task plaza model', () => {
  test('SquareResource 覆盖 bot/group/task 三态', () => {
    // 编译期断言：联合类型必须接受 'task'，否则 tsc 报错。
    const resources: SquareResource[] = ['bot', 'group', 'task'];
    expect(resources).toContain('task');
  });

  test('PlazaTaskStatus 为四种广场只读状态', () => {
    // 编译期断言：PlazaTaskStatus 必须恰好是这四个值。
    const statuses: PlazaTaskStatus[] = ['pending_claim', 'claimed', 'reviewing', 'completed'];
    expect(Object.keys(TASK_STATUS_CONFIG).sort()).toEqual([...statuses].sort());
  });

  test('TASK_STATUS_CONFIG 四态文案与 tone 固定映射', () => {
    expect(TASK_STATUS_CONFIG.pending_claim).toEqual({ label: '待认领', tone: 'warning' });
    expect(TASK_STATUS_CONFIG.claimed).toEqual({ label: '已认领', tone: 'brand' });
    expect(TASK_STATUS_CONFIG.reviewing).toEqual({ label: '待验收', tone: 'info' });
    expect(TASK_STATUS_CONFIG.completed).toEqual({ label: '已完成', tone: 'success' });
  });

  test('getPublicTaskStatusPresentation 返回与 TASK_STATUS_CONFIG 一致的 label/tone', () => {
    const statuses: PlazaTaskStatus[] = ['pending_claim', 'claimed', 'reviewing', 'completed'];
    for (const status of statuses) {
      expect(getPublicTaskStatusPresentation(status)).toEqual(TASK_STATUS_CONFIG[status]);
    }
  });
});
