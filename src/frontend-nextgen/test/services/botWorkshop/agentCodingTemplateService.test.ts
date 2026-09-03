import * as botTemplateController from '@/services/backendApi/bots/botTemplateController';
import { agentCodingTemplateService, supportsServiceBot } from '@/services/botWorkshop/agentCodingTemplateService';
import { afterEach, describe, expect, it, jest } from '@jest/globals';

const listTemplates = jest.spyOn(botTemplateController, 'listAgentCodingTemplates');

afterEach(() => {
  listTemplates.mockReset();
});

describe('agentCodingTemplateService', () => {
  it('保留内置应用 Bot，并将模板工厂返回的 applicationCoding 模板一并拼接', async () => {
    listTemplates.mockResolvedValue([
      {
        engine_type: 'claude_code',
        template_type: 'applicationCoding',
        template_config: {
          template_key: 'applicationCoding',
          template_version_id: 2800006,
          template_name: '应用 Bot',
          bot_template_config: {
            id: 2800006,
            template_key: 'applicationCoding',
            template_name: '应用 Bot',
            status: 'pre_published',
            template_category: 'official',
            custom_field_config: [],
          },
        },
      },
      {
        engine_type: 'claude_code',
        template_type: 'generalCC',
        template_config: {
          template_key: 'generalCC',
          template_version_id: 3000001,
          template_name: '大安全业务通用 Bot',
          support_engines: ['claude-code', 'codex', 'codefuse-codex', 'codefuse-antcc'],
          bot_template_config: {
            id: 3000001,
            template_key: 'generalCC',
            template_name: '大安全业务通用 Bot',
            status: 'online',
            template_category: 'official',
            custom_field_config: [],
          },
        },
      },
    ]);

    const templates = await agentCodingTemplateService.list();
    const applicationTemplates = templates.filter((item) => item.templateType === 'applicationCoding');

    expect(applicationTemplates).toHaveLength(2);
    expect(applicationTemplates[0]).toEqual(
      expect.objectContaining({
        key: 'app_coding',
        versionId: 'applicationCoding',
        name: '应用 Bot',
        source: 'official',
      }),
    );
    expect(applicationTemplates[1]).toEqual(
      expect.objectContaining({
        key: 'applicationCoding',
        versionId: '2800006',
        templateReleaseStage: 'whitelist',
      }),
    );
    expect(templates).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          key: 'generalCC',
          versionId: '3000001',
          templateType: 'generalCC',
          source: 'official',
          capabilityTags: expect.arrayContaining(['多引擎']),
        }),
      ]),
    );
  });

  it('只按模板能力位判断是否支持服务 Bot，应用 Coding Bot 始终不支持', () => {
    const template = (capabilities?: Record<string, unknown>) => ({
      key: 'architect',
      versionId: '2600004',
      name: '架构 Bot',
      engine: 'claude_code',
      source: 'official' as const,
      templateType: 'architect',
      fields: [],
      config: { capabilities },
      raw: {},
      capabilityTags: [],
    });

    expect(supportsServiceBot(template({ upgrade_service_bot: true }))).toBe(true);
    expect(supportsServiceBot(template({ upgrade_service_bot: false }))).toBe(false);
    expect(supportsServiceBot(template({ upgrade_service_bot: 'true' }))).toBe(false);
    expect(supportsServiceBot({ ...template({ upgrade_service_bot: true }), templateType: 'applicationCoding' })).toBe(
      false,
    );
  });

  it('兼容模板工厂将服务能力放在 template_config.capabilities 下', () => {
    expect(
      supportsServiceBot({
        key: 'architect',
        versionId: '2600004',
        name: '架构 Bot',
        engine: 'claude_code',
        source: 'official',
        templateType: 'architect',
        fields: [],
        config: {},
        raw: {
          template_config: { capabilities: { upgrade_service_bot: true } },
        },
        capabilityTags: [],
      }),
    ).toBe(true);
  });
});
