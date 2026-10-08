/** @jest-environment jsdom */
import { AgentCodingTemplateCard } from '@/components/BotWorkshop/CreateBotModal/agentCoding/AgentCodingTemplateCard';
import { getTemplateReleaseStage, type AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';
import { render, screen } from '@testing-library/react';

const makeTemplate = (overrides: Partial<AgentCodingTemplate> = {}): AgentCodingTemplate => ({
  key: 'template-key',
  versionId: 'version-1',
  name: '模板 Bot',
  description: '模板描述',
  engine: 'aicoding',
  templateType: 'generalCoding',
  source: 'official',
  fields: [],
  config: {},
  raw: {},
  capabilityTags: [],
  ...overrides,
});

test('官方推荐不展示负责人，描述使用较深的正文颜色', () => {
  render(
    <AgentCodingTemplateCard
      template={makeTemplate({ ownerName: '模板负责人' })}
      selected={false}
      onSelect={jest.fn()}
    />,
  );

  expect(screen.queryByText('负责人：模板负责人')).toBeNull();
  expect(screen.getByText('模板描述').className).toContain('text-foreground/70');
});

test('模板市场展示负责人，白名单状态在卡片右下角展示绶带', () => {
  render(
    <AgentCodingTemplateCard
      template={makeTemplate({
        source: 'market',
        ownerName: '模板负责人',
        templateReleaseStage: 'whitelist',
      })}
      selected={false}
      onSelect={jest.fn()}
    />,
  );

  expect(screen.getByText('负责人：模板负责人')).toBeTruthy();
  expect(screen.getByLabelText('白名单阶段').textContent).toContain('白名单');
});

test('在线模板不展示白名单绶带', () => {
  render(
    <AgentCodingTemplateCard
      template={makeTemplate({ templateReleaseStage: 'online' })}
      selected={false}
      onSelect={jest.fn()}
    />,
  );

  expect(screen.queryByLabelText('白名单阶段')).toBeNull();
});

test('白名单判断沿用旧版状态字段和优先级', () => {
  expect(getTemplateReleaseStage({ bot_template_config: { status: 'pre_published' } })).toBe('whitelist');
  expect(getTemplateReleaseStage({ bot_template_config: { status: 'whitelist' } })).toBe('whitelist');
  expect(getTemplateReleaseStage({ template_config: { bot_template_config: { status: 'online' } } })).toBe('online');
  expect(getTemplateReleaseStage({ bot_template_config: { status: 'published' } })).toBe('online');
  expect(
    getTemplateReleaseStage({
      bot_template_config: { status: 'pre_published' },
      template_config: { bot_template_config: { status: 'online' } },
    }),
  ).toBe('whitelist');
  expect(getTemplateReleaseStage({ status: 'whitelist' })).toBeUndefined();
});
