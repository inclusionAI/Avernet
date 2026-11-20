import {
  pollBotAuthStatus,
  updateBotExt,
} from '@/services/backendApi/bots/botController';
import { botWorkshopService } from '@/services/botWorkshop/botWorkshopService';
import type { BotCreateInput } from '@/services/botWorkshop';
import type { AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';
import { describe, expect, it } from '@jest/globals';

jest.mock('@/services/backendApi/bots/botController', () => ({
  createBot: jest.fn(),
  pollBotAuthStatus: jest.fn(),
  updateBotExt: jest.fn(),
}));

const mockedPollBotAuthStatus = pollBotAuthStatus as jest.MockedFunction<typeof pollBotAuthStatus>;
const mockedUpdateBotExt = updateBotExt as jest.MockedFunction<typeof updateBotExt>;

const template = {
  key: 'architect',
  versionId: 'v1',
  name: '架构 Bot',
  engine: 'aicoding',
  templateType: 'architect',
  source: 'official',
  fields: [{ field_key: 'architect_name', field_name: '架构域', field_type: 'input', required: true }],
  config: {},
  raw: {},
  capabilityTags: [],
} as AgentCodingTemplate;

describe('AgentCoding authorization completion', () => {
  it('registers an architectural Bot through ext after authorization succeeds', async () => {
    const request = {
      bot_name: '支付架构域 Bot',
      bot_desc: '',
      engine: 'aicoding',
      cluster_name: 'ACRA' as const,
      bot_type: 'personal' as const,
    };
    const agentCoding: BotCreateInput['agentCoding'] = {
      kind: 'template',
      template,
      values: { architect_name: '支付架构域' },
    };

    mockedPollBotAuthStatus.mockResolvedValue({
      code: 200000,
      data: {
        status: 'ISSUED',
        bot: {
          bot_id: 'bot-architect-1',
          bot_name: '支付架构域 Bot',
          engine: 'aicoding',
          status: 'ACTIVE',
        },
      },
    });
    mockedUpdateBotExt.mockResolvedValue({ code: 200000, data: {} });

    const result = await botWorkshopService.pollCreateAuthorization(
      'bot-architect-1',
      request,
      agentCoding,
    );

    expect(result.status).toBe('ISSUED');
    expect(result.afterCreateFailures).toBeUndefined();
    expect(mockedUpdateBotExt).toHaveBeenCalledWith('bot-architect-1', {
      arch_domain: '支付架构域',
      is_domain_bot: true,
    });
  });
});
