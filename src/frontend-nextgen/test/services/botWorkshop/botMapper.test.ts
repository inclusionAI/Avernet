import { mapBotDto, mapBotList } from '@/services/botWorkshop/botMapper';

describe('botMapper', () => {
  test('服务 Bot 详情仅返回 ACTIVE 且没有发布阶段时按草稿处理', () => {
    const result = mapBotDto({
      bot_id: 'service-draft-1',
      bot_name: '草稿服务 Bot',
      engine: 'openclaw',
      bot_type: 'service',
      status: 'ACTIVE',
      owner_entity_id: 'owner-1',
    });

    expect(result.item.serviceMode).toBe('service');
    expect(result.item.lifecycle).toBe('draft');
  });

  test('将接口字段映射为稳定领域模型', () => {
    const result = mapBotDto({
      bot_id: 'b-1',
      name: '示例 Bot',
      description: '描述',
      entity_type: 'team',
      space_id: 'space-1',
      active_engine: 'openclaw',
      bot_type: 'service',
      status: 'ACTIVE',
      publish_status: 'success',
      health_score: 96,
      lock: { status: 'owned-by-other', holder_name: '协作者' },
      owner_entity_id: 'user-owner-1',
    });

    expect(result.item.id).toBe('b-1');
    expect(result.item.ownership).toBe('team');
    expect(result.item.serviceMode).toBe('service');
    expect(result.item.runtime.engine).toBe('openclaw');
    expect(result.item.lifecycle).toBe('running');
    expect(result.item.healthScore).toBe(96);
    expect(result.item.ownerId).toBe('user-owner-1');
    expect(result.warnings).toEqual([]);
  });

  test('未知引擎进入安全只读画像并返回 warning', () => {
    const result = mapBotDto({ bot_id: 'b-2', active_engine: 'future_engine' });

    expect(result.item.runtime.engine).toBe('unknown');
    expect(result.item.completeness).toBe('partial');
    expect(result.item.runtime.capabilityProfile.canPublish).toBe(false);
    expect(result.warnings).toContain('未知引擎：future_engine');
  });

  test('claude_code Bot 在工坊可见可编辑，但不获得服务化发布能力', () => {
    const result = mapBotDto({
      bot_id: '20260824_ykapx915',
      bot_name: 'claudecode2',
      engine: 'claude_code',
      bot_type: 'personal',
      display_state: 'running',
    });

    expect(result.item.runtime.visibleInOpenCore).toBe(true);
    expect(result.item.runtime.capabilityProfile.canEdit).toBe(true);
    expect(result.item.runtime.capabilityProfile.canPublish).toBe(false);
  });

  test('支持从嵌套 space 对象读取 space_id', () => {
    const result = mapBotDto({
      bot_id: 'b-3',
      bot_type: 'personal',
      space: { space_id: 'space-nested', kind: 'team' },
      entity_id: 'entity-nested',
    } as never);

    expect(result.item.spaceId).toBe('space-nested');
    expect(result.item.spaceKind).toBe('team');
    expect(result.item.ownership).toBe('team');
    expect(result.item.harnessContext?.entityId).toBe('entity-nested');
  });

  test('复合键避免不同空间和 Bot 类型互相覆盖', () => {
    const result = mapBotList({
      items: [
        { bot_id: 'same', space_id: 'a', bot_type: 'personal' },
        { bot_id: 'same', space_id: 'b', bot_type: 'service' },
      ],
    });

    expect(result.items.map((item) => item.entityKey)).toEqual(['a:personal:same', 'b:service:same']);
  });

  test('库存 kind=service 时识别为服务化，不受底层 bot_type 影响', () => {
    const result = mapBotDto({
      bot_id: 'service-card-1',
      bot_type: 'personal',
      kind: 'service',
      engine: 'openclaw',
      status: 'running',
    });

    expect(result.item.serviceMode).toBe('service');
  });

  test('直接映射库存接口 edit_lock，不需要逐 Bot 补查锁接口', () => {
    const result = mapBotDto({
      bot_id: 'service-card-locked',
      owner_entity_id: 'owner-1',
      bot_type: 'service',
      kind: 'service',
      engine: 'openclaw',
      display_state: 'service_draft',
      edit_lock: {
        locked: true,
        holder_user_id: 'editor-1',
        holder_name: '协作者',
        has_collaborators: true,
        is_owner_holder: false,
        need_lock: true,
      },
    });

    expect(result.item.lock).toEqual({
      status: 'other',
      holderUserId: 'editor-1',
      holderName: '协作者',
      lockedAt: undefined,
    });
  });

  test.each([
    ['service_draft', 'draft'],
    ['service_deploying', 'deploying'],
    ['service_prestable', 'prestable'],
    ['service_staging', 'prestable'],
    ['service_online', 'running'],
    ['service_offline', 'offline'],
  ])('优先按后端 display_state=%s 映射服务 Bot 状态', (displayState, expected) => {
    const result = mapBotDto({
      bot_id: 'service-card-1',
      bot_type: 'service',
      kind: 'service',
      engine: 'openclaw',
      display_state: displayState,
      status: 'prestable',
      internal_status: 'validating',
    });

    expect(result.item.lifecycle).toBe(expected);
    expect(result.warnings).toEqual([]);
  });
});

describe('Agent Coding Bot runtime classification', () => {
  test('历史 aicoding 识别为 Agent Coding Bot', () => {
    expect(
      mapBotDto({ bot_id: 'legacy', engine: 'aicoding', template_type: 'anything' }).item.runtime.isAgentCodingBot,
    ).toBe(true);
  });

  test('applicationCoding 识别为 Agent Coding Bot', () => {
    expect(
      mapBotDto({ bot_id: 'application', engine: 'claude_code', template_type: 'applicationCoding' }).item.runtime
        .isAgentCodingBot,
    ).toBe(true);
  });

  test('claude_code 下非普通 CC 模板识别为 Agent Coding Bot', () => {
    expect(
      mapBotDto({ bot_id: 'template', engine: 'claude_code', template_type: 'architect' }).item.runtime
        .isAgentCodingBot,
    ).toBe(true);
    expect(
      mapBotDto({ bot_id: 'normal', engine: 'claude_code', template_type: 'normalCC' }).item.runtime.isAgentCodingBot,
    ).toBe(false);
  });
});
