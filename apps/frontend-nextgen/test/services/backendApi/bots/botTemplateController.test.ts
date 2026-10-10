import { getTemplateFactoryEnv, listAgentCodingTemplates } from '@/services/backendApi/bots/botTemplateController';
import * as httpClient from '@/services/backendApi/httpClient';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/httpClient', () => {
  const { jest: jestGlobals } = require('@jest/globals');
  return { backendRequest: jestGlobals.fn() };
});

const backendRequest = httpClient.backendRequest as jest.MockedFunction<typeof httpClient.backendRequest>;

beforeEach(() => {
  backendRequest.mockReset();
});

describe('botTemplateController', () => {
  it('uses POST and the PRE env for TeamClaw template pagination by default', async () => {
    backendRequest.mockResolvedValue({ items: [] });

    await listAgentCodingTemplates();

    expect(backendRequest).toHaveBeenCalledWith('/template-factory/bot-templates/available-tc-list', {
      method: 'POST',
      data: { env: 'pre', page: 1, pageSize: 50 },
      operation: 'list-agent-coding-templates',
    });
  });

  it('maps runtime LOCAL/DEV/PRE and local fallback to the template factory PRE env', () => {
    expect(getTemplateFactoryEnv('LOCAL')).toBe('pre');
    expect(getTemplateFactoryEnv('DEV')).toBe('pre');
    expect(getTemplateFactoryEnv('PRE')).toBe('pre');
    expect(getTemplateFactoryEnv(undefined)).toBe('pre');
  });

  it('maps production to the template factory prod env', () => {
    expect(getTemplateFactoryEnv('PROD')).toBe('prod');
  });
});
