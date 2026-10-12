import { getCapabilities } from '@/capabilities';
import { agentCodingTemplateService, supportsServiceBot, type AgentCodingTemplate } from './agentCodingTemplateService';
import type { AvernetBotCreateRequest, BotCreateInput, BotCreateSpace } from './types';

const PUBLIC_ENGINES = new Set(['openclaw', 'claude_code', 'aicoding', 'hermes', 'teclaw']);
const SERVICE_ENGINES = new Set(['openclaw', 'claude_code', 'teclaw']);

function nativeClaudeCodeSelectable(): boolean {
  return getCapabilities()
    .getBotEngineOptions()
    .value.some((option) => option.value === 'claude_code');
}

function configuredLocalUserId() {
  return typeof TEAMCLAW_OPENAPI_USER_ID === 'string' ? TEAMCLAW_OPENAPI_USER_ID.trim() : '';
}

export function personalCreateSpace(userId = configuredLocalUserId()): BotCreateSpace {
  const localUserId = userId.trim();
  return {
    id: localUserId ? `personal:${localUserId}` : '',
    name: '个人空间',
    ownership: 'personal',
    canCreate: Boolean(localUserId),
  };
}

export function validateBotCreate(input: BotCreateInput) {
  const name = input.name.trim();
  if (!name) throw new Error('请输入 Bot 名称');
  if (name.includes('@')) throw new Error('Bot 名称不能包含 @');
  if (name.length > 40) throw new Error('Bot 名称不能超过 40 个字符');
  if (!PUBLIC_ENGINES.has(input.engine)) throw new Error('请选择可用的公开引擎');
  if (input.scenario === 'cloud' && input.serviceMode === 'service') {
    if (input.engine === 'aicoding') {
      const template = input.agentCoding?.template as AgentCodingTemplate | undefined;
      if (!template) throw new Error('请选择 AgentCoding 模板');
      if (!supportsServiceBot(template)) throw new Error('当前模板未开启服务 Bot 能力');
    } else if (!SERVICE_ENGINES.has(input.engine)) {
      const engineName = input.engine === 'hermes' ? 'Hermes' : '当前引擎';
      throw new Error(`${engineName} 暂不支持服务化`);
    }
  }
  if (input.scenario === 'cloud' && !input.spaceId.trim()) throw new Error('请选择有效的归属空间');
  if (input.engine === 'aicoding') {
    const template = input.agentCoding?.template;
    if (!template) throw new Error('请选择 AgentCoding 模板');
    const templateError = agentCodingTemplateService.validate(template as never, input.agentCoding?.values ?? {});
    if (templateError) throw new Error(templateError);
  }
  if (
    input.scenario === 'cloud' &&
    input.ownership === 'personal' &&
    input.engine === 'claude_code' &&
    !input.agentCoding?.template &&
    !nativeClaudeCodeSelectable()
  )
    throw new Error('普通 Claude Code 请通过 AgentCoding 模板创建');
  if (input.agentCoding?.template && ['normal', 'normalCC'].includes(input.agentCoding.template.templateType))
    throw new Error('普通 Claude Code 模板不能走 AgentCoding 创建');
}

export function toBotCreateRequest(input: BotCreateInput): AvernetBotCreateRequest {
  validateBotCreate(input);
  const engine = input.agentCoding?.template?.engine || input.engine;
  return {
    bot_name: input.name.trim(),
    bot_desc: input.description.trim(),
    engine,
    cluster_name: engine === 'teclaw' ? 'ANDC' : 'ACRA',
    bot_type: input.serviceMode === 'service' ? 'service' : 'personal',
    space_id: input.spaceId || undefined,
    ...(input.agentCoding?.template
      ? agentCodingTemplateService.toCreateFields(
          input.agentCoding.template as never,
          input.agentCoding.values,
          input.name,
        )
      : {}),
  };
}
