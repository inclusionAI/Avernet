import type { BotChatDetail, BotChatObservation, BotChatPage, BotChatSummary } from '@/domain/botChats';
import type {
  BotChatDetailDto,
  BotChatObservationDto,
  BotChatPageDto,
  BotChatSessionDto,
} from '@/services/backendApi/bots/botChatController';

const SECRET_KEYS = new Set([
  'authorization',
  'token',
  'accesstoken',
  'refreshtoken',
  'cookie',
  'secret',
  'password',
  'passwd',
  'pwd',
  'apikey',
  'privatekey',
  'secretkey',
  'xiamtoken',
]);
const normalizedKey = (key: string) => key.toLowerCase().replace(/[^a-z0-9]/g, '');

export function maskBotChatSecrets(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(maskBotChatSecrets);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, item]) => [
      key,
      SECRET_KEYS.has(normalizedKey(key)) ? '***' : maskBotChatSecrets(item),
    ]),
  );
}

const modelFrom = (dto: BotChatSessionDto) => {
  const attributes = dto.metadata?.attributes;
  const candidate = attributes?.['gen_ai.request.model'] ?? attributes?.['gen_ai.response.model'];
  return typeof candidate === 'string' ? candidate : undefined;
};

export function mapBotChatSummary(dto: BotChatSessionDto): BotChatSummary {
  return {
    id: dto.id,
    botId: dto.bot_id ?? undefined,
    botName: dto.bot_name ?? undefined,
    timestamp: dto.timestamp,
    sessionId: dto.session_id ?? undefined,
    sessionKey: dto.session_key ?? undefined,
    name: dto.name || '未命名 Trace',
    input: maskBotChatSecrets(dto.input),
    outputPreview: dto.output_preview ?? undefined,
    bizScene: dto.biz_scene ?? undefined,
    bizTaskId: dto.biz_task_id ?? undefined,
    groupId: dto.group_id ?? undefined,
    model: modelFrom(dto),
    status: dto.status ?? 'SUCCESS',
    latencyMs: dto.latency_ms ?? 0,
    totalTokens: dto.total_tokens ?? 0,
    totalCost: dto.total_cost ?? 0,
  };
}

function mapObservation(dto: BotChatObservationDto): BotChatObservation {
  return {
    id: dto.id,
    type: dto.type,
    name: dto.name || dto.type,
    modelName: dto.model_name ?? undefined,
    input: maskBotChatSecrets(dto.input),
    output: maskBotChatSecrets(dto.output),
    metadata: maskBotChatSecrets(dto.metadata) as Record<string, unknown> | undefined,
    latencyMs: dto.latency_ms ?? 0,
    totalTokens: dto.total_tokens ?? 0,
    totalCost: dto.total_cost ?? 0,
    children: (dto.children ?? []).map(mapObservation),
  };
}

export function mapBotChatDetail(dto: BotChatDetailDto): BotChatDetail {
  return {
    ...mapBotChatSummary(dto),
    output: maskBotChatSecrets(dto.output),
    metadata: maskBotChatSecrets(dto.metadata) as Record<string, unknown> | undefined,
    observations: (dto.observations ?? []).map(mapObservation),
  };
}

export function mapBotChatPage(dto: BotChatPageDto): BotChatPage {
  return {
    items: dto.sessions.map(mapBotChatSummary),
    total: dto.total,
    page: dto.page,
    limit: dto.limit,
    hasMore: dto.has_more,
  };
}
