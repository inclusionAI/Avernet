import type { AvernetBotCreateRequest, BotCreateInput } from '@/domain/botWorkshop';
import type { BackendApiPage, BackendUnknownRecord } from '@/services/backendApi/types';

const seedBots: BackendUnknownRecord[] = [
  {
    bot_id: 'mock-bot-001',
    bot_name: '项目知识管家',
    bot_desc: '汇总项目资料并回答团队常见问题。',
    engine: 'openclaw',
    bot_type: 'personal',
    status: 'ACTIVE',
    owner_entity_id: 'personal-space',
  },
  {
    bot_id: 'mock-bot-002',
    bot_name: '服务巡检助手',
    bot_desc: '定时检查服务状态并整理异常信息。',
    // 云端服务化样例：仅用当前形态可选引擎（Open Core 见 getBotEngineOptions），不用 teclaw 等内部引擎。
    engine: 'openclaw',
    bot_type: 'service',
    status: 'ACTIVE',
    publish_status: 'success',
    owner_entity_id: 'team-space',
    entity_type: 'team',
    health_score: 96,
    healthy_instances: 3,
    total_instances: 3,
  },
  {
    bot_id: 'mock-bot-003',
    bot_name: '本地文档助手',
    bot_desc: '在本机环境中整理离线文档。',
    engine: 'openclaw',
    bot_type: 'desktop',
    status: 'ACTIVE',
    owner_entity_id: 'personal-space',
  },
  {
    bot_id: 'mock-bot-004',
    bot_name: '研究提纲助手',
    bot_desc: '将研究目标拆解为可执行的调研提纲。',
    engine: 'claude_code',
    bot_type: 'personal',
    status: 'ACTIVE',
    owner_entity_id: 'personal-space',
  },
  {
    bot_id: 'mock-bot-005',
    bot_name: '发布准备助手',
    bot_desc: '检查发布材料并生成变更摘要。',
    engine: 'openclaw',
    bot_type: 'service',
    status: 'ACTIVE',
    publish_status: 'draft',
    owner_entity_id: 'team-space',
    entity_type: 'team',
  },
  {
    bot_id: 'mock-bot-006',
    bot_name: '数据核对助手',
    bot_desc: '核对结构化数据中的缺失项与异常项。',
    // 个人非服务化样例（claude_code 原生个人引擎）。
    engine: 'claude_code',
    bot_type: 'personal',
    status: 'PENDING',
    owner_entity_id: 'personal-space',
  },
];

let bots = seedBots.map((item) => ({ ...item }));

const delay = () =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, 120);
  });

export const botWorkshopMockGateway = {
  async list(): Promise<BackendApiPage<BackendUnknownRecord>> {
    await delay();
    return { items: bots.map((item) => ({ ...item })), total: bots.length, page: 1, pageSize: 100, hasMore: false };
  },
  async create(input: BotCreateInput, request?: AvernetBotCreateRequest): Promise<BackendUnknownRecord> {
    await delay();
    const id = `mock-bot-${Date.now()}`;
    const item: BackendUnknownRecord =
      input.scenario === 'local'
        ? {
            bot_id: id,
            bot_name: input.name.trim(),
            bot_desc: input.description.trim(),
            engine: input.engine,
            bot_type: 'desktop',
            status: 'ACTIVE',
            owner_entity_id: 'personal-space',
          }
        : {
            bot_id: id,
            bot_name: request?.bot_name,
            bot_desc: request?.bot_desc,
            engine: request?.engine,
            cluster_name: request?.cluster_name,
            bot_type: request?.bot_type,
            status: request?.bot_type === 'service' ? 'ACTIVE' : 'PENDING',
            publish_status: request?.bot_type === 'service' ? 'draft' : undefined,
            owner_entity_id: input.spaceId,
            entity_type: input.ownership === 'team' ? 'team' : 'personal',
          };
    bots = [item, ...bots];
    return { ...item };
  },
  reset() {
    bots = seedBots.map((item) => ({ ...item }));
  },
};
