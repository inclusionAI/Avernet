import { updateBotExt } from '@/services/backendApi/bots/botController';
import {
  resolveAfterCreateActions,
  runAfterCreateActions,
} from '@/services/botWorkshop/agentCodingAfterCreateService';
import type { AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';
import type { BotTemplateField } from '@/services/backendApi/bots/botTemplateController';
import { beforeEach, describe, expect, it } from '@jest/globals';

jest.mock('@/services/backendApi/bots/botController', () => ({
  updateBotExt: jest.fn(),
}));

const mockedUpdateBotExt = updateBotExt as jest.MockedFunction<typeof updateBotExt>;

function makeTemplate(
  fields: BotTemplateField[],
  raw: Record<string, unknown> = {},
  config: Record<string, unknown> = {},
): AgentCodingTemplate {
  return {
    key: 'architect',
    versionId: 'v1',
    name: '架构 Bot',
    description: '',
    engine: 'aicoding',
    templateType: 'architect',
    source: 'official',
    fields,
    config,
    raw: raw as AgentCodingTemplate['raw'],
    capabilityTags: [],
  };
}

beforeEach(() => {
  mockedUpdateBotExt.mockClear();
  mockedUpdateBotExt.mockResolvedValue({ code: 200000, data: {} });
});

describe('AgentCoding after-create actions', () => {
  it('handles an explicit architect_name declaration once', () => {
    const template = makeTemplate(
      [{ field_key: 'architect_name', field_name: '架构域', field_type: 'input', required: true }],
      { afterCreate: ['architect_name'] },
    );

    expect(resolveAfterCreateActions(template)).toEqual([
      { key: 'architect_name', retryable: true },
    ]);
  });

  it('infers the architect ext action from an architect_name field', async () => {
    const template = makeTemplate([
      { field_key: 'architectName', field_name: '架构域', field_type: 'input', required: true },
    ]);

    expect(resolveAfterCreateActions(template)).toEqual([
      { key: 'update_architect_bot_ext', retryable: true },
    ]);

    const failures = await runAfterCreateActions({
      botId: 'bot-architect-1',
      ownerId: '001',
      template,
      values: { architectName: '支付架构域' },
    });

    expect(failures).toEqual([]);
    expect(mockedUpdateBotExt).toHaveBeenCalledWith('bot-architect-1', {
      arch_domain: '支付架构域',
      is_domain_bot: true,
    });
  });

  it('does not send the ext request when the submitted architecture-domain name is empty', async () => {
    const template = makeTemplate([
      { field_key: 'architect_name', field_name: '架构域', field_type: 'input', required: true },
    ]);

    const failures = await runAfterCreateActions({
      botId: 'bot-architect-1',
      ownerId: '001',
      template,
      values: { architect_name: '   ' },
    });

    expect(failures).toEqual([]);
    expect(mockedUpdateBotExt).not.toHaveBeenCalled();
  });

  it('does not invent architect initialization for templates without an architect field', () => {
    const template = makeTemplate([{ field_key: 'model', field_name: '模型', field_type: 'string' }]);

    expect(resolveAfterCreateActions(template)).toEqual([]);
  });

  it('keeps DIMA initialization out of the after-create action whitelist', () => {
    const template = makeTemplate([], {}, { capabilities: { dima_workspace: true } });

    expect(resolveAfterCreateActions(template)).toEqual([]);
  });

  it('ignores an unsupported afterCreate declaration', () => {
    const template = makeTemplate([], { afterCreate: ['ensure_dima_workspace'] });

    expect(resolveAfterCreateActions(template)).toEqual([]);
  });
});
