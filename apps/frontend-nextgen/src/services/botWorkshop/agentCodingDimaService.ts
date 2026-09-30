import { getBot } from '@/services/backendApi/bots/botController';
import * as cronController from '@/services/backendApi/legacyCronController';
import { createDimaWorkspace } from '@/services/backendApi/legacyDimaController';
import { isEnvelopeFailure } from '@/services/backendApi/types';
import type { AgentCodingTemplate } from './agentCodingTemplateService';

const CRON_NAME_PREFIX = '7*24小时自动生码__';
const CRON_SCHEDULE = '0 10,14,18 * * *';
const CRON_TIMEZONE = 'Asia/Shanghai';
const CRON_TIMEOUT_SECS = 1800;
const MAX_TASK_NUM = 3;
const DEFAULT_MODEL = 'GLM-5.2';
const DEFAULT_RUNTIME = 'claude-code';
const DEFAULT_CUSTOM_MESSAGE = '使用mcporter技能调用dimamcpserver工具读取${url}需求后，进行代码开发完成需求';

type RecordLike = Record<string, unknown>;
const record = (value: unknown): RecordLike =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as RecordLike) : {};
const text = (value: unknown) => (typeof value === 'string' && value.trim() ? value.trim() : undefined);

function capabilities(template: AgentCodingTemplate): RecordLike {
  const config = record(template.config);
  const nested = record(config.bot_template_config);
  return { ...record(nested.advanced_config), ...record(config.capabilities) };
}

export function shouldEnsureDimaWorkspace(template: AgentCodingTemplate) {
  return capabilities(template).dima_workspace === true;
}

export function isHosted24x7(template: AgentCodingTemplate, values: RecordLike) {
  const value =
    values.is_hosted_24x7 ??
    values.is_hosted_7x24 ??
    values.enable_24x7_hosting ??
    values.enable_7x24_hosting ??
    template.config.is_hosted_24x7;
  return value === true || value === 1 || value === '1' || value === 'true';
}

export function cronName(botId: string) {
  return `${CRON_NAME_PREFIX}${botId}`;
}

function unwrapList(data: unknown): RecordLike[] {
  if (Array.isArray(data)) return data.filter((item): item is RecordLike => Boolean(item) && typeof item === 'object');
  const object = record(data);
  if (Array.isArray(object.items))
    return object.items.filter((item): item is RecordLike => Boolean(item) && typeof item === 'object');
  if (Array.isArray(object.data))
    return object.data.filter((item): item is RecordLike => Boolean(item) && typeof item === 'object');
  return [];
}

async function hasCron(botId: string, ownerId?: string) {
  const response = await cronController.listTasks({ bot_id: botId, owner_id: ownerId });
  if (isEnvelopeFailure(response)) throw new Error(response.message || '查询 7×24 定时任务失败');
  return unwrapList(response.data).some((task) => task.name === cronName(botId));
}

function getBotStatus(data: unknown) {
  const root = record(data);
  const nested = record(root.data);
  return String(root.status ?? nested.status ?? '').toUpperCase();
}

export async function waitForBotActive(botId: string, options: { intervalMs?: number; timeoutMs?: number } = {}) {
  const intervalMs = options.intervalMs ?? 3000;
  const timeoutMs = options.timeoutMs ?? 5 * 60 * 1000;
  const deadline = Date.now() + timeoutMs;
  let lastStatus = '';
  while (Date.now() <= deadline) {
    const response = await getBot(botId);
    lastStatus = getBotStatus(response.data);
    if (lastStatus === 'ACTIVE') return;
    if (['FAILED', 'OFFLINE', 'RELEASED', 'RECYCLED'].includes(lastStatus)) {
      throw new Error(`Bot 激活失败（${lastStatus}），未创建 7×24 定时任务`);
    }
    await new Promise<void>((resolve) => {
      setTimeout(resolve, intervalMs);
    });
  }
  throw new Error(`等待 Bot 激活超时（${lastStatus || '未知状态'}），未创建 7×24 定时任务`);
}

function buildCommand(botId: string, ownerId: string, dimaSpaceId: string, template: AgentCodingTemplate) {
  const workflow = text(template.config.devflow_workflow && record(template.config.devflow_workflow).name);
  const segments = [
    `space:${dimaSpaceId}`,
    `user:${ownerId}`,
    `agent:${botId}`,
    'kind:autoInitiate',
    `maxTaskNum:${MAX_TASK_NUM}`,
  ];
  if (workflow) segments.splice(4, 0, `workflow:${workflow}`);
  else segments.push(`custom_message:${DEFAULT_CUSTOM_MESSAGE}`);
  return `查询dima空间${dimaSpaceId}的待开发需求，开启7*24小时自动研发|${segments.join('|')}`;
}

export async function ensureDimaWorkspaceAndCron({
  botId,
  ownerId,
  template,
  values,
}: {
  botId: string;
  ownerId?: string;
  template: AgentCodingTemplate;
  values: RecordLike;
}) {
  if (!shouldEnsureDimaWorkspace(template)) return;
  const response = await createDimaWorkspace(botId, ownerId);
  if (isEnvelopeFailure(response)) throw new Error(response.message || 'DIMA 空间创建失败');
  const dimaSpaceId = text(record(response.data).dima_space_id);
  if (!dimaSpaceId) throw new Error('DIMA 空间创建成功，但未返回空间 ID');
  if (!isHosted24x7(template, values)) return;
  await waitForBotActive(botId);
  if (await hasCron(botId, ownerId)) return;
  const config = record(template.config);
  const model = text(config.model) || text(config.default_model) || DEFAULT_MODEL;
  const runtime = text(config.runtime) || DEFAULT_RUNTIME;
  const result = await cronController.createTask({
    bot_id: botId,
    owner_id: ownerId,
    name: cronName(botId),
    schedule: CRON_SCHEDULE,
    command: buildCommand(botId, ownerId || '', dimaSpaceId, template),
    model,
    runtime,
    timezone: CRON_TIMEZONE,
    timeout_secs: CRON_TIMEOUT_SECS,
    notify: { enabled: false, user_ids: [] },
  });
  if (isEnvelopeFailure(result)) throw new Error(result.message || '7×24 定时任务创建失败');
}
