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

  test('bot_type 大小写不敏感地推导 serviceMode（与 lifecycle 口径一致）', () => {
    const result = mapBotDto({
      bot_id: 'service-case-1',
      bot_name: '大写类型服务 Bot',
      engine: 'openclaw',
      bot_type: 'SERVICE',
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

  test('详情接口的 ext.avatar_url 也映射为 Bot 头像', () => {
    const result = mapBotDto({
      bot_id: 'avatar-bot',
      bot_name: '头像 Bot',
      engine: 'openclaw',
      ext: { avatar_url: 'data:image/svg+xml;base64,avatar' },
    });

    expect(result.item.avatarUrl).toBe('data:image/svg+xml;base64,avatar');
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

  test('Coding Bot 从模板快照解析 BCN 能力并写入 runtime', () => {
    const result = mapBotDto({
      bot_id: 'coding-bcn',
      engine_type: 'claude_code',
      template_type: 'generalCC',
      engine_properties: {
        template_config: {
          capabilities: { enable_bcn_network: true },
        },
      },
    });

    expect(result.item.runtime.bcnEnabled).toBe(true);
  });

  test('Coding Bot 读取结构化 BCN 能力', () => {
    const result = mapBotDto({
      bot_id: 'coding-bcn-structured',
      engine_type: 'claude_code',
      template_type: 'generalCC',
      engine_properties: {
        template_config: {
          capabilities: { bcn: { enabled: true } },
        },
      },
    });

    expect(result.item.runtime.bcnEnabled).toBe(true);
  });

  test('Coding Bot 读取 capability_tags 中的 BCN', () => {
    const result = mapBotDto({
      bot_id: 'coding-bcn-tag',
      engine_type: 'claude_code',
      template_type: 'generalCC',
      engine_properties: {
        template_config: {
          capability_tags: ['BCN', '成员协作'],
        },
      },
    });

    expect(result.item.runtime.bcnEnabled).toBe(true);
  });

  test('Coding Bot 未声明或明确关闭 BCN 时不开放协作能力', () => {
    const missing = mapBotDto({
      bot_id: 'coding-bcn-missing',
      engine_type: 'claude_code',
      template_type: 'generalCC',
      engine_properties: {
        template_config: { capabilities: { member_management: true } },
      },
    });
    const disabled = mapBotDto({
      bot_id: 'coding-bcn-disabled',
      engine_type: 'claude_code',
      template_type: 'generalCC',
      engine_properties: {
        template_config: { capabilities: { enable_bcn_network: false } },
      },
    });

    expect(missing.item.runtime.bcnEnabled).toBe(false);
    expect(disabled.item.runtime.bcnEnabled).toBe(false);
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

  test('团队 inventory 缺少 entity_id 时不把 owner_entity_id 当成实体路由标识', () => {
    const result = mapBotDto({
      bot_id: 'team-bot-1',
      bot_name: '团队协作者 Bot',
      engine: 'openclaw',
      bot_type: 'personal',
      status: 'ACTIVE',
      owner_entity_id: 'owner-user-1',
      space: { space_id: '252', name: 'Fixture Team', kind: 'team' },
    });

    expect(result.item.ownerId).toBe('owner-user-1');
    expect(result.item.harnessContext?.entityId).toBeUndefined();
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

  test.each([
    [
      'Bot 初始化优先于草稿发布态',
      { status: 'PENDING', publish: { id: 1, status: 'DRAFT', version: 1 }, display_state: 'service_draft' },
      'deploying',
    ],
    [
      '没有发布记录的服务 Bot 初始化中',
      { status: 'PROVISIONING', publish: null, display_state: 'service_draft' },
      'deploying',
    ],
    [
      '发布失败优先于旧部署中展示态',
      { status: 'ACTIVE', publish: { id: 1, status: 'FAILED', version: 1 }, display_state: 'service_deploying' },
      'failed',
    ],
    [
      '发布成功优先于旧草稿展示态',
      { status: 'ACTIVE', publish: { id: 1, status: 'SUCCESS', version: 1 }, display_state: 'service_draft' },
      'running',
    ],
  ])('%s', (_label, fields, expected) => {
    const result = mapBotDto({
      bot_id: 'service-structured-status',
      bot_type: 'service',
      kind: 'service',
      engine: 'openclaw',
      ...fields,
    });

    expect(result.item.lifecycle).toBe(expected);
  });

  test('个人 Bot 仍使用原 display_state 兼容语义', () => {
    const result = mapBotDto({
      bot_id: 'personal-legacy-status',
      bot_type: 'personal',
      engine: 'openclaw',
      status: 'ACTIVE',
      display_state: 'running',
    });

    expect(result.item.lifecycle).toBe('running');
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

  test('服务管理的 engine_form=AICODING 标记识别为 Agent Coding Bot', () => {
    const result = mapBotDto({
      bot_id: 'engine-form-marked',
      engine: 'claude_code',
      template_config: { engine_form: 'AICODING' },
    });

    expect(result.item.runtime.isAgentCodingBot).toBe(true);
  });

  test('Bot 管理详情的嵌套 engine_form=AICODING 标记识别为 Agent Coding Bot', () => {
    const result = mapBotDto({
      bot_id: 'nested-engine-form-marked',
      engine: 'claude_code',
      engine_properties: { template_config: { engine_form: 'aicoding' } },
    });

    expect(result.item.runtime.isAgentCodingBot).toBe(true);
  });

  test('applicationCoding 引擎值识别为 Agent Coding Bot', () => {
    const result = mapBotDto({ bot_id: 'application-engine', engine: 'applicationCoding', template_type: '' });

    expect(result.item.runtime.isAgentCodingBot).toBe(true);
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
