import { getBot } from '@/services/backendApi/bots/botController';
import * as cronController from '@/services/backendApi/legacyCronController';
import { createDimaWorkspace } from '@/services/backendApi/legacyDimaController';
import { ensureDimaWorkspaceAndCron, shouldEnsureDimaWorkspace } from '@/services/botWorkshop/agentCodingDimaService';
import type { AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';

jest.mock('@/services/backendApi/bots/botController', () => ({ getBot: jest.fn() }));
jest.mock('@/services/backendApi/legacyDimaController', () => ({ createDimaWorkspace: jest.fn() }));
jest.mock('@/services/backendApi/legacyCronController', () => ({
  listTasks: jest.fn(),
  createTask: jest.fn(),
}));

const mockedGetBot = getBot as jest.MockedFunction<typeof getBot>;
const mockedCreateDima = createDimaWorkspace as jest.MockedFunction<typeof createDimaWorkspace>;
const mockedListTasks = cronController.listTasks as jest.MockedFunction<typeof cronController.listTasks>;
const mockedCreateTask = cronController.createTask as jest.MockedFunction<typeof cronController.createTask>;

function template(overrides: Record<string, unknown> = {}): AgentCodingTemplate {
  return {
    key: 'applicationCoding',
    versionId: '2800006',
    name: '应用 Bot',
    engine: 'claude_code',
    templateType: 'applicationCoding',
    source: 'official',
    fields: [],
    config: { capabilities: { dima_workspace: true }, ...overrides },
    raw: {},
    capabilityTags: [],
  };
}

describe('agentCodingDimaService', () => {
  beforeEach(() => jest.clearAllMocks());

  it('识别 template_config.capabilities 和 bot_template_config.advanced_config 的 DIMA 能力', () => {
    expect(shouldEnsureDimaWorkspace(template())).toBe(true);
    expect(
      shouldEnsureDimaWorkspace(
        template({ capabilities: undefined, bot_template_config: { advanced_config: { dima_workspace: true } } }),
      ),
    ).toBe(true);
  });

  it('按 DIMA → ACTIVE → 幂等检查 → 7×24 Cron 顺序执行', async () => {
    mockedCreateDima.mockResolvedValue({ success: true, data: { dima_space_id: 'dima-1' } });
    mockedGetBot.mockResolvedValue({ success: true, data: { status: 'ACTIVE' } });
    mockedListTasks.mockResolvedValue({ success: true, data: { items: [] } });
    mockedCreateTask.mockResolvedValue({ success: true, data: {} });

    await ensureDimaWorkspaceAndCron({
      botId: 'bot-1',
      ownerId: 'u-1',
      template: template(),
      values: { is_hosted_24x7: 1 },
    });

    expect(mockedCreateDima).toHaveBeenCalledWith('bot-1', 'u-1');
    expect(mockedGetBot).toHaveBeenCalledWith('bot-1');
    expect(mockedListTasks).toHaveBeenCalledWith({ bot_id: 'bot-1', owner_id: 'u-1' });
    expect(mockedCreateTask).toHaveBeenCalledWith(
      expect.objectContaining({
        bot_id: 'bot-1',
        owner_id: 'u-1',
        name: '7*24小时自动生码__bot-1',
        schedule: '0 10,14,18 * * *',
        command: expect.stringContaining('space:dima-1'),
      }),
    );
  });

  it('没有开启 7×24 时只创建 DIMA，不轮询和创建 Cron', async () => {
    mockedCreateDima.mockResolvedValue({ success: true, data: { dima_space_id: 'dima-1' } });
    await ensureDimaWorkspaceAndCron({ botId: 'bot-1', template: template(), values: {} });
    expect(mockedCreateDima).toHaveBeenCalled();
    expect(mockedGetBot).not.toHaveBeenCalled();
    expect(mockedCreateTask).not.toHaveBeenCalled();
  });

  it('已有同名任务时不重复创建', async () => {
    mockedCreateDima.mockResolvedValue({ success: true, data: { dima_space_id: 'dima-1' } });
    mockedGetBot.mockResolvedValue({ success: true, data: { status: 'ACTIVE' } });
    mockedListTasks.mockResolvedValue({ success: true, data: { items: [{ name: '7*24小时自动生码__bot-1' }] } });
    await ensureDimaWorkspaceAndCron({ botId: 'bot-1', template: template(), values: { is_hosted_24x7: true } });
    expect(mockedCreateTask).not.toHaveBeenCalled();
  });
});
