/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * ServiceBot Mock Service - 服务型 Bot 发布相关 Mock 数据服务
 * 用于在接口未就绪时提供模拟数据
 */

import type {
  Bot,
  BotListByOwnerResponse,
  BotStatusResponse,
  SearchBotsResponse,
} from '../backend-api/BotController';
import type {
  AcquireLockResponse,
  AddCollaboratorRequest,
  AddCollaboratorResponse,
  CheckPermissionResponse,
  CollaboratorInfo,
  DeleteServiceBotResponse,
  GetLockInfoResponse,
  GetPublishDetailResponse,
  GetReadOnlyTreeResponse,
  ListCollaboratorsParams,
  ListCollaboratorsResponse,
  LockInfo,
  ProcessPublishResponse,
  PublishRecord,
  PublishStatus,
  ReadOnlyRule,
  ReleaseLockResponse,
  RemoveCollaboratorResponse,
  StealLockResponse,
  UpdateCollaboratorRequest,
  UpdateCollaboratorResponse,
  UpgradeBotTypeResponse,
  UpgradePublishResponse,
} from '../backend-api/ServiceBotController';

/**
 * 带发布信息的 Bot（用于搜索接口）
 */
interface ServiceBotWithPublish extends Bot {
  publish?: {
    id: number;
    source_bot_pk: number;
    publish_bot_id: string;
    status: string;
    version: number;
    ext?: Record<string, any>;
  };
}

// 模拟延迟
const delay = (ms: number = 500): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(() => resolve(), ms);
  });

// 内存存储发布单数据
const publishRecords = new Map<number, PublishRecord>();
let publishIdCounter = 1000;

// 生成 mock 发布单
function createMockPublishRecord(
  id: number,
  overrides: Partial<PublishRecord> = {},
): PublishRecord {
  return {
    id,
    source_bot_pk: 123,
    source_bot_id: `bot-service-${id}`,
    publish_bot_id: `bot-publish-${id}`,
    name: overrides.name || `Service Bot ${id}`,
    owner_id: 'user001',
    status: (overrides.status as PublishStatus) || 'draft',
    version: overrides.version || 1,
    last_pub_id: overrides.last_pub_id || null,
    ext: {
      build_path: `/builds/${id}`,
      device_count: 2,
      ...overrides.ext,
    },
  };
}

// 生成 mock Bot
function createMockBot(overrides: Partial<Bot> = {}): Bot {
  const id = overrides.id || Date.now();
  const botId = overrides.bot_id || `bot-${id}`;
  return {
    id,
    bot_id: botId,
    bot_name: overrides.bot_name || 'Test Bot',
    bot_desc: overrides.bot_desc || null,
    avatar_url: overrides.avatar_url || null,
    entity_id: overrides.entity_id || 'user001',
    entity_type: overrides.entity_type || 'staff',
    creator_id: overrides.creator_id || 'user001',
    owner_id: overrides.owner_id || 'user001',
    engine_types: overrides.engine_types || ['openclaw'],
    status: overrides.status || 'ACTIVE',
    binding_id: overrides.binding_id || 10001,
    gmt_create: overrides.gmt_create || new Date().toISOString(),
    gmt_modified: overrides.gmt_modified || new Date().toISOString(),
    modifier_id: overrides.modifier_id,
    share_policy: overrides.share_policy || null,
    is_delete: overrides.is_delete || 0,
    active_engine: overrides.active_engine || 'openclaw',
    device_id: overrides.device_id || `device-${botId}`,
    env: overrides.env,
    owner_name: overrides.owner_name || 'Test User',
    public: overrides.public || '0',
    ext: overrides.ext,
    engine_paths: overrides.engine_paths || { openclaw: '/engines/openclaw' },
    bot_work_dir: overrides.bot_work_dir || `/work/${botId}`,
    bot_type: overrides.bot_type || 'personal',
  };
}

// 生成带发布信息的 Service Bot（搜索结果格式）
function createMockServiceBot(
  id: number,
  overrides: Partial<ServiceBotWithPublish> = {},
): ServiceBotWithPublish {
  const botId = overrides.bot_id || `bot-service-${id}`;
  const publishId = overrides.publish?.id || id;
  const publishStatus = overrides.publish?.status || 'success';
  const version = overrides.publish?.version || 1;

  return {
    id,
    bot_id: botId,
    bot_name: overrides.bot_name || `Service Bot ${id}`,
    bot_desc: overrides.bot_desc || null,
    avatar_url: overrides.avatar_url || null,
    entity_id: overrides.entity_id || 'user001',
    entity_type: overrides.entity_type || 'staff',
    creator_id: overrides.creator_id || 'user001',
    owner_id: overrides.owner_id || 'user001',
    engine_types: overrides.engine_types || ['openclaw'],
    status: overrides.status || 'ACTIVE',
    binding_id: overrides.binding_id ?? 10000 + id,
    gmt_create:
      overrides.gmt_create ||
      `2024-01-${String(id).padStart(2, '0')}T00:00:00Z`,
    gmt_modified:
      overrides.gmt_modified ||
      `2024-01-${String(id).padStart(2, '0')}T00:00:00Z`,
    modifier_id: overrides.modifier_id,
    share_policy: overrides.share_policy || null,
    is_delete: 0,
    active_engine: 'openclaw',
    device_id: `device-${botId}`,
    env: overrides.env,
    owner_name: overrides.owner_name || '张三',
    public: overrides.public || '1',
    ext: overrides.ext,
    engine_paths: { openclaw: '/engines/openclaw' },
    bot_work_dir: `/work/${botId}`,
    bot_type: 'service',
    publish: {
      id: publishId,
      source_bot_pk: id,
      publish_bot_id: `${botId}_pub_${version}`,
      status: publishStatus,
      version,
      ext: overrides.publish?.ext || {},
    },
  };
}

/**
 * 推进发布流程（Mock）
 * POST /api/service-bot/publish/process
 */
export async function processPublishMock(
  publishId: number,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  deviceCount: number = 1,
): Promise<ProcessPublishResponse> {
  await delay(800);

  let record = publishRecords.get(publishId);
  if (!record) {
    record = createMockPublishRecord(publishId, {
      status: 'draft',
      name: `Service Bot ${publishId}`,
    });
    publishRecords.set(publishId, record);
  }

  // 状态流转逻辑
  let responseData: ProcessPublishResponse['data'];

  switch (record.status) {
    case 'draft':
      record.status = 'built';
      responseData = {
        publish_id: record.id,
        status: 'built',
        message: '构建成功',
        build_path: `/builds/${record.id}/output`,
      };
      break;
    case 'built':
      record.status = 'success';
      responseData = {
        publish_id: record.id,
        status: 'success',
        message: '发布成功',
        bot_uuid: `uuid-${record.id}`,
        device_binding_id: Math.floor(Math.random() * 10000),
      };
      break;
    case 'success':
      responseData = {
        publish_id: record.id,
        status: 'success',
        message: '已是最新版本',
        bot_uuid: `uuid-${record.id}`,
      };
      break;
    default:
      responseData = {
        publish_id: record.id,
        status: record.status,
        message: '未知状态',
      };
  }

  return {
    success: true,
    message: '操作成功',
    data: responseData,
  };
}

/**
 * 升级发布单（Mock）
 * POST /api/service-bot/publish/upgrade
 */
export async function upgradePublishMock(
  publishId: number,
): Promise<UpgradePublishResponse> {
  await delay(600);

  const oldRecord = publishRecords.get(publishId);

  if (!oldRecord) {
    return {
      success: false,
      message: '发布单不存在',
      error_code: 404,
      data: {} as PublishRecord,
    };
  }

  if (oldRecord.status !== 'success') {
    return {
      success: false,
      message: '只有发布成功的 Bot 才能升级',
      error_code: 400,
      data: {} as PublishRecord,
    };
  }

  const newRecord = createMockPublishRecord(++publishIdCounter, {
    name: oldRecord.name,
    source_bot_id: oldRecord.source_bot_id,
    status: 'draft',
    version: oldRecord.version + 1,
    last_pub_id: oldRecord.id,
  });
  publishRecords.set(newRecord.id, newRecord);

  return {
    success: true,
    message: '升级成功',
    data: newRecord,
  };
}

/**
 * 查询发布记录详情（Mock）
 * GET /api/service-bot/publish/{publish_id}
 */
export async function getPublishDetailMock(
  publishId: number,
): Promise<GetPublishDetailResponse> {
  await delay(300);

  let record = publishRecords.get(publishId);

  if (!record) {
    // 如果没有，创建一个默认的
    record = createMockPublishRecord(publishId, {
      status: 'draft',
      name: `Service Bot ${publishId}`,
    });
    publishRecords.set(publishId, record);
  }

  return {
    success: true,
    message: '查询成功',
    data: record,
  };
}

/**
 * 同步发布流程状态（Mock）
 * POST /api/service-bot/publish/{publish_id}/sync
 */
export async function syncPublishStatusMock(publishId: number): Promise<{
  success: boolean;
  message: string;
  data: {
    publish_id: number;
    status: string;
    message: string;
    build_path: string | null;
    bot_uuid: string;
    baas_publish_id: string;
    device_binding_id: number;
    data: {
      publish_id: number;
      status: string;
      current_stage: string;
      overall_progress: {
        total_devices: number;
        processed_devices: number;
        failed_devices: number;
        progress_percentage: number;
      };
      device_details: any[];
      failed_devices: any[];
    };
  };
  error_code: null;
}> {
  await delay(500);

  const record = publishRecords.get(publishId);
  const baasId = Math.floor(Math.random() * 10000000).toString();

  return {
    success: true,
    message: '发布进度同步成功，状态: SUCCESS',
    data: {
      publish_id: publishId,
      status: record?.status || 'success',
      message: '发布进度同步成功，状态: SUCCESS',
      build_path:
        record?.status === 'draft' ? null : `/builds/${publishId}/output`,
      bot_uuid: `uuid-${publishId}`,
      baas_publish_id: baasId,
      device_binding_id: Math.floor(Math.random() * 10000),
      data: {
        publish_id: parseInt(baasId, 10),
        status: 'SUCCESS',
        current_stage: 'PROD',
        overall_progress: {
          total_devices: 100,
          processed_devices: 100,
          failed_devices: 0,
          progress_percentage: 100.0,
        },
        device_details: [],
        failed_devices: [],
      },
    },
    error_code: null,
  };
}

/**
 * 搜索服务型 Bot（Mock）
 * GET /api/bots/search/by-keyword
 * 返回带 publish 字段的 Bot 列表，包含所有发布状态和 Bot 状态的组合
 */
export async function searchServiceBotsMock(): Promise<SearchBotsResponse> {
  await delay(400);

  // 所有发布状态与 Bot 状态的组合
  const mockBots: ServiceBotWithPublish[] = [
    // DRAFT + PENDING
    createMockServiceBot(10, {
      bot_id: '20260427_tehoot3i',
      bot_name: '草稿态-Bot初始化中',
      owner_name: '张三',
      status: 'PENDING',
      public: '0',
      publish: {
        id: 10,
        source_bot_pk: 10,
        publish_bot_id: 'pub_10',
        status: 'draft',
        version: 1,
        ext: {},
      },
    }),
    // DRAFT + ACTIVE
    createMockServiceBot(11, {
      bot_id: 'bot_draft_active',
      bot_name: '草稿态-Bot正常',
      owner_name: '张三',
      status: 'ACTIVE',
      public: '0',
      publish: {
        id: 11,
        source_bot_pk: 11,
        publish_bot_id: 'pub_11',
        status: 'draft',
        version: 1,
        ext: {},
      },
    }),
    // DRAFT + FAILED
    createMockServiceBot(12, {
      bot_id: 'bot_draft_failed',
      bot_name: '草稿态-Bot故障',
      owner_name: '李四',
      status: 'FAILED',
      public: '0',
      publish: {
        id: 12,
        source_bot_pk: 12,
        publish_bot_id: 'pub_12',
        status: 'draft',
        version: 1,
        ext: {},
      },
    }),
    // DRAFT + RELEASED
    createMockServiceBot(13, {
      bot_id: 'bot_draft_released',
      bot_name: '草稿态-Bot休眠',
      owner_name: '王五',
      status: 'RELEASED',
      public: '0',
      publish: {
        id: 13,
        source_bot_pk: 13,
        publish_bot_id: 'pub_13',
        status: 'draft',
        version: 1,
        ext: {},
      },
    }),
    // BUILDING + ACTIVE
    createMockServiceBot(14, {
      bot_id: 'bot_building_active',
      bot_name: '构建中-Bot正常',
      owner_name: '李四',
      status: 'ACTIVE',
      public: '0',
      publish: {
        id: 14,
        source_bot_pk: 14,
        publish_bot_id: 'pub_14',
        status: 'building',
        version: 1,
        ext: { build_progress: 45 },
      },
    }),
    // BUILT + ACTIVE
    createMockServiceBot(15, {
      bot_id: 'bot_built_active',
      bot_name: '已构建-Bot正常',
      owner_name: '王五',
      status: 'ACTIVE',
      public: '0',
      publish: {
        id: 15,
        source_bot_pk: 15,
        publish_bot_id: 'pub_15',
        status: 'built',
        version: 1,
        ext: { build_path: '/builds/15' },
      },
    }),
    // VALIDATE_PUB + ACTIVE
    createMockServiceBot(16, {
      bot_id: 'bot_validate_pub_active',
      bot_name: '预发发布中-Bot正常',
      owner_name: '赵六',
      status: 'ACTIVE',
      public: '0',
      publish: {
        id: 16,
        source_bot_pk: 16,
        publish_bot_id: 'pub_16',
        status: 'validate_pub',
        version: 1,
        ext: { stage: 'PRE' },
      },
    }),
    // VALIDATING + ACTIVE
    createMockServiceBot(17, {
      bot_id: '20260427_tehoot3i',
      bot_name: '预发已发布-Bot正常',
      owner_name: '钱七',
      status: 'ACTIVE',
      public: '1',
      publish: {
        id: 17,
        source_bot_pk: 17,
        publish_bot_id: 'pub_17',
        status: 'validating',
        version: 1,
        ext: {},
      },
    }),
    // ONLINE_PUB + ACTIVE
    createMockServiceBot(18, {
      bot_id: 'bot_online_pub_active',
      bot_name: '线上发布中-Bot正常',
      owner_name: '孙八',
      status: 'ACTIVE',
      public: '1',
      publish: {
        id: 18,
        source_bot_pk: 18,
        publish_bot_id: 'pub_18',
        status: 'online_pub',
        version: 2,
        ext: { stage: 'PROD' },
      },
    }),
    // SUCCESS + ACTIVE
    createMockServiceBot(19, {
      bot_id: 'bot_success_active',
      bot_name: '发布成功-Bot正常',
      owner_name: '周九',
      status: 'ACTIVE',
      public: '1',
      publish: {
        id: 19,
        source_bot_pk: 19,
        publish_bot_id: 'pub_19',
        status: 'success',
        version: 3,
        ext: { bot_uuid: 'uuid-19' },
      },
    }),
    // SUCCESS + PENDING
    createMockServiceBot(20, {
      bot_id: 'bot_success_pending',
      bot_name: '发布成功-Bot初始化中',
      owner_name: '吴十',
      status: 'PENDING',
      public: '1',
      publish: {
        id: 20,
        source_bot_pk: 20,
        publish_bot_id: 'pub_20',
        status: 'success',
        version: 1,
        ext: {},
      },
    }),
    // UPGRADED + ACTIVE
    createMockServiceBot(21, {
      bot_id: 'bot_upgraded_active',
      bot_name: '已升级-Bot正常',
      owner_name: '郑一',
      status: 'ACTIVE',
      public: '1',
      publish: {
        id: 21,
        source_bot_pk: 21,
        publish_bot_id: 'pub_21',
        status: 'upgraded',
        version: 4,
        ext: { last_pub_id: 100 },
      },
    }),
    // RELEASED + RELEASED
    createMockServiceBot(22, {
      bot_id: 'bot_released_released',
      bot_name: '已下线-Bot休眠',
      owner_name: '陈二',
      status: 'RELEASED',
      public: '1',
      publish: {
        id: 22,
        source_bot_pk: 22,
        publish_bot_id: 'pub_22',
        status: 'released',
        version: 2,
        ext: {},
      },
    }),
    // FAILED + FAILED
    createMockServiceBot(23, {
      bot_id: 'bot_failed_failed',
      bot_name: '发布失败-Bot故障',
      owner_name: '刘三',
      status: 'FAILED',
      public: '0',
      publish: {
        id: 23,
        source_bot_pk: 23,
        publish_bot_id: 'pub_23',
        status: 'failed',
        version: 1,
        ext: { error: '构建超时' },
      },
    }),
    // BUILDING + PENDING
    createMockServiceBot(24, {
      bot_id: 'bot_building_pending',
      bot_name: '构建中-Bot初始化中',
      owner_name: '黄四',
      status: 'PENDING',
      public: '0',
      publish: {
        id: 24,
        source_bot_pk: 24,
        publish_bot_id: 'pub_24',
        status: 'building',
        version: 1,
        ext: {},
      },
    }),
    // VALIDATING + PENDING
    createMockServiceBot(25, {
      bot_id: 'bot_validating_pending',
      bot_name: '预发已发布-Bot初始化中',
      owner_name: '林五',
      status: 'PENDING',
      public: '1',
      publish: {
        id: 25,
        source_bot_pk: 25,
        publish_bot_id: 'pub_25',
        status: 'validating',
        version: 2,
        ext: {},
      },
    }),
  ];

  return {
    success: true,
    message: '查询成功',
    error_code: 0,
    data: {
      total: 16,
      items: mockBots,
    },
  };
}

/**
 * 获取 Bot 列表（Mock）
 * GET /api/bots/by-owner
 */
export async function getBotsByOwnerMock(
  userId?: string,
): Promise<BotListByOwnerResponse> {
  await delay(400);

  const mockBots: Bot[] = [
    createMockBot({
      id: 1,
      bot_id: 'bot-default-001',
      bot_name: 'My Default Bot',
      bot_desc: '我的默认数字分身',
      status: 'ACTIVE',
      binding_id: 10001,
      bot_type: 'personal',
      public: '0',
      gmt_create: '2024-01-01T00:00:00Z',
      gmt_modified: '2024-01-15T12:00:00Z',
    }),
    createMockBot({
      id: 2,
      bot_id: 'bot-service-001',
      bot_name: 'Service Bot Alpha',
      bot_desc: '服务型 Bot - 客服助手',
      status: 'ACTIVE',
      binding_id: 10002,
      bot_type: 'service',
      public: '1',
      gmt_create: '2024-02-01T00:00:00Z',
      gmt_modified: '2024-02-10T10:00:00Z',
    }),
    createMockBot({
      id: 3,
      bot_id: 'bot-service-002',
      bot_name: 'Service Bot Beta',
      bot_desc: '服务型 Bot - 技术支持',
      engine_types: ['openclaw', 'moltis'],
      status: 'PENDING',
      binding_id: null,
      bot_type: 'service',
      public: '0',
      gmt_create: '2024-03-01T00:00:00Z',
      gmt_modified: '2024-03-01T00:00:00Z',
    }),
  ];

  return {
    success: true,
    message: '查询成功',
    error_code: 0,
    data: {
      total: mockBots.length,
      items: mockBots,
      default_bot: {
        entity_id: userId || 'user001',
        bot_id: 'bot-default-001',
        entity_type: 'staff',
        engine_type: 'openclaw',
        bot_work_dir: '/work/bot-default-001',
        engine_paths: {
          openclaw: '/engines/openclaw',
        },
      },
      engine_types: ['openclaw', 'moltis'],
    },
  };
}

/**
 * 查询 Bot 状态（Mock）
 * GET /api/bots/{bot_id}/status
 */
export async function getBotStatusMock(
  botId: string,
): Promise<BotStatusResponse> {
  await delay(300);

  return {
    success: true,
    data: {
      bot_id: botId,
      bot_status: 'ACTIVE',
      binding_status: 'ACTIVE',
      device_id: `device-${botId}`,
      device_provider: 'mock',
      error_message: null,
      is_ready: true,
      ext: {
        start_status: 'SUCCEEDED',
        start_message: '启动成功',
      },
    },
  };
}

/**
 * 获取 Bot 详情（Mock）
 * GET /api/bots/{bot_id}
 */
export async function getBotDetailMock(botId: string) {
  await delay(300);

  return {
    success: true,
    message: '查询成功',
    error_code: 0,
    data: {
      bot: createMockBot({
        bot_id: botId,
        bot_name: botId.includes('service') ? 'Service Bot' : 'Personal Bot',
        bot_type: botId.includes('service') ? 'service' : 'personal',
      }),
    },
  };
}

/**
 * 重启 Bot（Mock）
 * POST /api/bots/{bot_id}/restart
 */
export async function restartBotMock(botId: string) {
  await delay(1500);

  return {
    success: true,
    message: '重启成功，Bot 正在初始化',
    error_code: 0,
    data: createMockBot({
      bot_id: botId,
      status: 'PENDING',
      binding_id: null,
    }),
  };
}

/**
 * 删除 Bot（Mock）
 * DELETE /api/bots/{bot_id}
 */
export async function deleteBotMock() {
  await delay(800);

  return {
    success: true,
    message: '删除成功',
    error_code: 0,
    data: null,
  };
}

/**
 * 创建 Bot（Mock）
 * POST /api/bots
 */
export async function createBotMock(params: {
  bot_name?: string;
  bot_desc?: string;
  bot_type?: 'personal' | 'service';
  engine_type?: string;
}) {
  await delay(1000);

  const newBotId = `bot-${Date.now()}`;

  return {
    success: true,
    message: '创建成功',
    error_code: 0,
    data: {
      bot: createMockBot({
        bot_id: newBotId,
        bot_name: params.bot_name || 'New Bot',
        bot_desc: params.bot_desc || null,
        bot_type: params.bot_type || 'personal',
        engine_types: params.engine_type
          ? [params.engine_type as any]
          : ['openclaw'],
        status: 'PENDING',
        binding_id: null,
      }),
    },
  };
}

/**
 * 升级 Bot 为服务型（Mock）
 * POST /api/service-bot/publish/upgrade_bot_type
 */
export async function upgradeBotTypeMock(
  botId: string,
): Promise<UpgradeBotTypeResponse> {
  await delay(800);

  console.log('[Mock] upgradeBotType:', botId);

  // 创建新的发布单记录
  const newPublishId = ++publishIdCounter;
  const publishRecord: PublishRecord = {
    id: newPublishId,
    source_bot_pk: Date.now(),
    source_bot_id: botId,
    publish_bot_id: `${botId}_service`,
    name: `Service Bot ${newPublishId}`,
    owner_id: 'user001',
    status: 'draft',
    version: 1,
    last_pub_id: null,
    ext: {},
  };
  publishRecords.set(newPublishId, publishRecord);

  return {
    success: true,
    message: 'Bot 已升级为服务型',
    error_code: null,
    data: {
      bot: {
        bot_id: botId,
        bot_type: 'service',
      },
      publish_record: {
        id: newPublishId,
        bot_id: botId,
        owner_id: 'user001',
        name: `Service Bot ${newPublishId}`,
        status: 'draft',
      },
    },
  };
}

/**
 * 删除服务型 Bot（Mock）
 * POST /api/service-bot/publish/{publish_id}/delete
 */
export async function deleteServiceBotMock(
  publishId: number,
): Promise<DeleteServiceBotResponse> {
  await delay(600);

  console.log('[Mock] deleteServiceBot:', publishId);

  const record = publishRecords.get(publishId);

  // 只有草稿状态才能删除
  if (record && record.status !== 'draft') {
    return {
      success: false,
      message: '只有草稿状态的服务 Bot 才能删除',
      error_code: 400,
      data: null,
    };
  }

  // 删除记录
  publishRecords.delete(publishId);

  return {
    success: true,
    message: '删除成功',
    error_code: null,
    data: {
      deleted: true,
    },
  };
}

// ======================== 协作 & 用户权限 Mock ========================

/** 当前 mock 用户 ID（与 SkillMembersTab mock 风格保持一致） */
const MOCK_CURRENT_USER = 'user001';
/** 当前 mock 用户花名（用于持锁人展示） */
const MOCK_CURRENT_USER_NAME = '斩秋';

/** mock 协作者主键自增 */
let collaboratorIdSeq = 1;

/** 协作者数据：key="botId:ownerId" -> CollaboratorInfo[] */
const collaboratorsStore = new Map<string, CollaboratorInfo[]>();

/** 自定义只读规则：botId -> ReadOnlyRule[] */
const readOnlyRulesStore = new Map<string, ReadOnlyRule[]>();

/** 锁状态：key="botId:ownerId" -> LockInfo | null（null 表示未锁定） */
const lockStore = new Map<string, LockInfo | null>();

const lockKey = (botId: string, ownerId: string) => `${botId}:${ownerId}`;

// ----- 协作者管理 -----

export async function listCollaboratorsMock(
  params: ListCollaboratorsParams,
): Promise<ListCollaboratorsResponse> {
  await delay(300);
  const key = lockKey(params.bot_id, params.owner_id);
  const all = collaboratorsStore.get(key) || [];
  const filtered = params.role
    ? all.filter((c) => c.role === params.role)
    : all;
  console.log('[Mock] listCollaborators:', params, filtered);
  return {
    success: true,
    message: '查询成功',
    error_code: 200,
    data: { collaborators: filtered },
  };
}

export async function addCollaboratorMock(
  body: AddCollaboratorRequest,
): Promise<AddCollaboratorResponse> {
  await delay(400);
  const key = lockKey(body.bot_id, body.owner_id);
  const list = collaboratorsStore.get(key) || [];
  if (list.some((c) => c.user_id === body.user_id)) {
    return {
      success: false,
      message: '协作者已存在',
      error_code: 409,
      data: null,
    };
  }
  const now = new Date().toISOString();
  const newCollab: CollaboratorInfo = {
    id: collaboratorIdSeq++,
    bot_pk: 100,
    bot_id: body.bot_id,
    owner_id: body.owner_id,
    user_id: body.user_id,
    user_name: body.user_name ?? null,
    role: body.role ?? 'admin',
    operator_id: MOCK_CURRENT_USER,
    gmt_create: now,
    gmt_modified: now,
  };
  collaboratorsStore.set(key, [...list, newCollab]);
  console.log('[Mock] addCollaborator:', newCollab);
  return {
    success: true,
    message: '协作者添加成功',
    error_code: 200,
    data: newCollab,
  };
}

export async function updateCollaboratorMock(
  body: UpdateCollaboratorRequest,
): Promise<UpdateCollaboratorResponse> {
  await delay(300);
  for (const [key, list] of collaboratorsStore.entries()) {
    const idx = list.findIndex((c) => c.id === body.id);
    if (idx >= 0) {
      const next = {
        ...list[idx],
        ...(body.user_id !== undefined ? { user_id: body.user_id } : {}),
        ...(body.user_name !== undefined ? { user_name: body.user_name } : {}),
        ...(body.role !== undefined ? { role: body.role } : {}),
        gmt_modified: new Date().toISOString(),
      };
      const newList = [...list];
      newList[idx] = next;
      collaboratorsStore.set(key, newList);
      return {
        success: true,
        message: '协作者更新成功',
        error_code: 200,
        data: next,
      };
    }
  }
  return {
    success: false,
    message: '协作者不存在',
    error_code: 404,
    data: null,
  };
}

export async function removeCollaboratorMock(
  id: number,
): Promise<RemoveCollaboratorResponse> {
  await delay(300);
  for (const [key, list] of collaboratorsStore.entries()) {
    const next = list.filter((c) => c.id !== id);
    if (next.length !== list.length) {
      collaboratorsStore.set(key, next);
      return {
        success: true,
        message: '协作者移除成功',
        error_code: 200,
        data: { deleted: true },
      };
    }
  }
  return {
    success: false,
    message: '协作者不存在',
    error_code: 404,
    data: null,
  };
}

export async function checkPermissionMock(
  botId: string,
  ownerId: string,
  userId?: string,
): Promise<CheckPermissionResponse> {
  await delay(200);
  const target = userId || MOCK_CURRENT_USER;
  if (target === ownerId) {
    return {
      success: true,
      message: 'ok',
      error_code: 200,
      data: { has_permission: true, level: 'OWNER', level_value: 3 },
    };
  }
  const list = collaboratorsStore.get(lockKey(botId, ownerId)) || [];
  const role = list.find((c) => c.user_id === target)?.role;
  if (role === 'admin') {
    return {
      success: true,
      message: 'ok',
      error_code: 200,
      data: { has_permission: true, level: 'ADMIN', level_value: 2 },
    };
  }
  return {
    success: true,
    message: 'ok',
    error_code: 200,
    data: { has_permission: false, level: 'NONE', level_value: 0 },
  };
}

// ----- 协作锁 -----

export async function acquireLockMock(
  botId: string,
  ownerId: string,
): Promise<AcquireLockResponse> {
  await delay(300);
  const key = lockKey(botId, ownerId);
  const cur = lockStore.get(key);

  if (cur && cur.holder_user_id !== MOCK_CURRENT_USER) {
    console.log('[Mock] acquireLock conflict:', key, cur);
    return {
      success: true,
      message: '锁被其他用户持有',
      error_code: 200,
      data: { acquired: false, lock: cur },
    };
  }

  // 重入或新建
  const lock: LockInfo = cur ?? {
    id: Math.floor(Math.random() * 100000),
    lock_key: key,
    holder_user_id: MOCK_CURRENT_USER,
    holder_name: MOCK_CURRENT_USER_NAME,
    gmt_create: new Date().toISOString(),
  };
  lockStore.set(key, lock);
  console.log('[Mock] acquireLock ok:', key, lock);
  return {
    success: true,
    message: '获取锁成功',
    error_code: 200,
    data: { acquired: true, lock },
  };
}

export async function releaseLockMock(
  botId: string,
  ownerId: string,
  force?: boolean,
): Promise<ReleaseLockResponse> {
  await delay(200);
  const key = lockKey(botId, ownerId);
  const cur = lockStore.get(key);
  if (!cur) {
    return {
      success: false,
      message: '锁不存在',
      error_code: 404,
      data: null,
    };
  }
  if (cur.holder_user_id !== MOCK_CURRENT_USER && !force) {
    return {
      success: false,
      message: '非持有者无法释放',
      error_code: 403,
      data: null,
    };
  }
  lockStore.set(key, null);
  return {
    success: true,
    message: '释放锁成功',
    error_code: 200,
    data: { released: true },
  };
}

export async function getLockInfoMock(
  botId: string,
  ownerId: string,
): Promise<GetLockInfoResponse> {
  await delay(200);
  const cur = lockStore.get(lockKey(botId, ownerId));
  const collabs = collaboratorsStore.get(lockKey(botId, ownerId)) || [];
  const hasCollaborators = collabs.length > 0;
  const isOwner = cur ? cur.holder_user_id === ownerId : false;
  const needLock = hasCollaborators;
  return {
    success: true,
    message: cur ? '查询成功' : 'Bot 未被锁定',
    error_code: 200,
    data: {
      locked: !!cur,
      lock: cur ?? null,
      has_collaborators: hasCollaborators,
      is_owner: isOwner,
      need_lock: needLock,
    },
  };
}

export async function stealLockMock(
  botId: string,
  ownerId: string,
): Promise<StealLockResponse> {
  await delay(300);
  const key = lockKey(botId, ownerId);
  const lock: LockInfo = {
    id: Date.now(),
    lock_key: key,
    holder_user_id: MOCK_CURRENT_USER,
    holder_name: MOCK_CURRENT_USER_NAME,
    gmt_create: new Date().toISOString(),
  };
  lockStore.set(key, lock);
  return {
    success: true,
    message: '抢锁成功',
    error_code: 200,
    data: { stolen: true, lock },
  };
}

// ----- 沙箱只读规则 -----

const DEFAULT_READONLY_RULES: ReadOnlyRule[] = [
  { path: '/home/admin/.openclaw/openclaw.json', rule_type: 'file' },
  {
    path: '/home/admin/.openclaw/workspace/config/mcporter.json',
    rule_type: 'file',
  },
  { path: '/home/admin/.openclaw/workspace/*.md', rule_type: 'glob' },
];

export async function getReadOnlyTreeMock(
  botId: string,
  path?: string,
): Promise<GetReadOnlyTreeResponse> {
  await delay(300);
  const base_path = '/home/admin/.openclaw';
  const custom = readOnlyRulesStore.get(botId) || [];
  const items =
    !path || path === ''
      ? [
          {
            name: 'workspace',
            path: 'workspace',
            is_dir: true,
            is_readonly_default: false,
            is_readonly_custom: false,
          },
          {
            name: 'openclaw.json',
            path: 'openclaw.json',
            is_dir: false,
            is_readonly_default: true,
            is_readonly_custom: false,
          },
          {
            name: 'agents',
            path: 'agents',
            is_dir: true,
            is_readonly_default: false,
            is_readonly_custom: false,
          },
        ]
      : path === 'workspace'
      ? [
          {
            name: 'config',
            path: 'workspace/config',
            is_dir: true,
            is_readonly_default: false,
            is_readonly_custom: custom.some(
              (r) => r.path === `${base_path}/workspace/config`,
            ),
          },
          {
            name: 'data',
            path: 'workspace/data',
            is_dir: true,
            is_readonly_default: false,
            is_readonly_custom: custom.some(
              (r) => r.path === `${base_path}/workspace/data`,
            ),
          },
          {
            name: 'README.md',
            path: 'workspace/README.md',
            is_dir: false,
            is_readonly_default: true,
            is_readonly_custom: false,
          },
        ]
      : [];

  return {
    success: true,
    message: 'OK',
    error_code: 200,
    data: {
      base_path,
      items,
      default_rules: DEFAULT_READONLY_RULES,
      custom_rules: custom,
    },
  };
}

export async function saveReadOnlyRulesMock(
  botId: string,
  rules: ReadOnlyRule[],
): Promise<any> {
  await delay(400);
  readOnlyRulesStore.set(botId, rules);
  return {
    success: true,
    message: 'OK',
    error_code: 200,
    data: { bot_id: botId, ext: { read_only_rules: rules } },
  };
}
