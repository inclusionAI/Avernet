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

  test('Coding Bot 卡片画像保留模板名称', () => {
    const result = mapBotDto({
      bot_id: 'architect-1',
      engine_type: 'claude_code',
      template_type: 'generalCC',
      engine_properties: {
        template_config: {
          template_name: '架构 Bot',
          bot_template_config: { template_name: '架构 Bot' },
        },
      },
    });

    expect(result.item.runtime.templateName).toBe('架构 Bot');
  });

  test('应用 Coding Bot 缺少接口模板名称时使用固定模板名称', () => {
    const result = mapBotDto({
      bot_id: 'application-1',
      engine_type: 'claude_code',
      template_type: 'applicationCoding',
    });

    expect(result.item.runtime.templateName).toBe('应用 Bot');
  });

  test('历史个人 Coding Bot 缺少接口模板名称时使用固定模板名称', () => {
    const result = mapBotDto({
      bot_id: 'personal-coding-1',
      engine_type: 'claude_code',
      template_type: 'personalCoding',
    });

    expect(result.item.runtime.templateName).toBe('个人 Coding Bot');
  });

  test('普通 Claude Code 不把模板名称作为卡片模板标签', () => {
    const result = mapBotDto({
      bot_id: 'normal-1',
      engine_type: 'claude_code',
      template_type: 'normalCC',
      template_name: '普通 CC',
    });

    expect(result.item.runtime.isAgentCodingBot).toBe(false);
    expect(result.item.runtime.templateName).toBe('普通 CC');
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

  test.each([
    ['应用 Coding Bot 不允许服务化', { template_type: 'applicationCoding', engine: 'claude_code' }, false],
    [
      '模板能力开启时允许服务化',
      {
        template_type: 'generalCC',
        engine: 'claude_code',
        engine_properties: { template_config: { capabilities: { upgrade_service_bot: true } } },
      },
      true,
    ],
    [
      '历史个人 Coding Bot 未声明能力时兼容允许服务化',
      { template_type: 'personalCoding', engine: 'claude_code' },
      true,
    ],
    [
      '其他 Coding 模板未声明能力时不允许服务化',
      { template_type: 'generalCC', engine: 'claude_code', bot_type: 'personal' },
      false,
    ],
    [
      '模板明确关闭能力时不允许服务化',
      {
        template_type: 'generalCC',
        engine: 'claude_code',
        engine_properties: { template_config: { capabilities: { upgrade_service_bot: false } } },
      },
      false,
    ],
  ])('%s', (_label, dto, expected) => {
    const result = mapBotDto({ bot_id: 'coding-service-capability', bot_type: 'personal', ...dto });

    expect(result.item.canUpgradeToService).toBe(expected);
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

  test('映射服务 Bot 卡片版本与后端唯一 card_id', () => {
    const result = mapBotDto({
      bot_id: 'service-1',
      card_id: 'service-1:publication:22',
      kind: 'service',
      publication_version: 2,
      live_version: 1,
    });

    expect(result.item).toEqual(
      expect.objectContaining({
        cardId: 'service-1:publication:22',
        entityKey: 'service-1:publication:22',
        publicationVersion: 2,
        liveVersion: 1,
      }),
    );
  });

  test('FAILED 库存状态映射为创建失败而不是未知', () => {
    const result = mapBotDto({ bot_id: 'failed-1', display_state: 'failed', status: 'FAILED' });

    expect(result.item.lifecycle).toBe('failed');
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
    expect(
      mapBotDto({ bot_id: 'general', engine: 'claude_code', template_type: 'generalCC' }).item.runtime.isAgentCodingBot,
    ).toBe(true);
  });
});
