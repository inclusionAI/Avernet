import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { clearCdnConfig, getLibraryCdn } from '@/services/bcs/libraryCdnInjector';
import { botEditorService } from '@/services/botWorkshop/botEditorService';

jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: {
    listSkills: jest.fn(),
    listSkillSetResources: jest.fn(),
    listBotMcps: jest.fn(),
    listMcpServers: jest.fn(),
    listRepositorySkills: jest.fn(),
    listSkillCenterSkills: jest.fn(),
    listConsumableSpaceSkills: jest.fn(),
    createSkillCenterReferences: jest.fn(),
    listSkillCenterReferences: jest.fn(),
    listResources: jest.fn(),
    listRenderScreens: jest.fn(),
    listRoutines: jest.fn(),
    getEngineConfig: jest.fn(),
    getEngineStatus: jest.fn(),
    getApprovalConfig: jest.fn(),
    listSkillSetSkills: jest.fn(),
    getMcpPermission: jest.fn(),
    setSkillSetMcp: jest.fn(),
    uploadSkillFolder: jest.fn(),
    getCallerContext: jest.fn(),
    updateMcpCallType: jest.fn(),
  },
}));

const controller = botEditorController as jest.Mocked<typeof botEditorController>;

beforeEach(() => {
  jest.clearAllMocks();
  clearCdnConfig();
  controller.listSkills.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.listSkillSetResources.mockResolvedValue({
    data: [{ id: '600005', name: '默认能力集', is_default: true, is_active: true, mcps: [], clis: [] }],
  });
  controller.listBotMcps.mockResolvedValue({ data: [] });
  controller.listMcpServers.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.listRepositorySkills.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.listSkillCenterSkills.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.listConsumableSpaceSkills.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.listResources.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.listRenderScreens.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.listRoutines.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.getEngineConfig.mockResolvedValue({ data: {} });
  controller.getEngineStatus.mockResolvedValue({ data: { engine: 'openclaw', active_connections: 0, running: false } });
  controller.listSkillSetSkills.mockRejectedValue(new Error('404 Not found'));
});

it('聚合加载能力集 MCP 和 CLI，仅对缺失的 Skill 关系补充查询', async () => {
  controller.listSkillSetResources.mockResolvedValue({
    data: [
      {
        id: '600005',
        name: '默认能力集',
        is_default: true,
        is_active: true,
        mcps: [{ server_code: 'mcp.weather', name: '天气 MCP' }],
        clis: [{ cli_code: 'claude', name: 'Claude CLI' }],
      },
    ],
  });
  controller.listSkillSetSkills.mockResolvedValue({
    data: [{ skill_id: 'skill-1', name: '代码 Skill' }],
  });

  const result = await botEditorService.load('20260806_wg6wkrk4');

  expect(result.skillSets).toEqual([
    expect.objectContaining({
      id: '600005',
      skills: [expect.objectContaining({ id: 'skill-1' })],
      mcps: [expect.objectContaining({ serverCode: 'mcp.weather' })],
      clis: [expect.objectContaining({ code: 'claude' })],
    }),
  ]);
  expect(controller.listSkillSetResources).toHaveBeenCalledTimes(1);
  expect(controller.listSkillSetSkills).toHaveBeenCalledTimes(1);
  expect(result.errors).toBe(0);
});

it('Skill 子资源失败时仍保留聚合接口返回的能力集、MCP 和 CLI', async () => {
  const result = await botEditorService.load('20260806_wg6wkrk4');

  expect(result.skillSets).toEqual([expect.objectContaining({ id: '600005', skills: [], mcps: [], clis: [] })]);
  expect(result.errors).toBe(1);
});

it('首屏不加载市场候选，用户打开添加能力时再一次性加载', async () => {
  controller.listSkillSetSkills.mockResolvedValue({ data: [] });

  await botEditorService.load('bot-1');

  expect(controller.listMcpServers).not.toHaveBeenCalled();
  expect(controller.listRepositorySkills).not.toHaveBeenCalled();
  expect(controller.listConsumableSpaceSkills).not.toHaveBeenCalled();

  await botEditorService.loadCapabilityCandidates('bot-1', '12');

  expect(controller.listBotMcps).toHaveBeenCalledWith('bot-1');
  expect(controller.listMcpServers).toHaveBeenCalledTimes(1);
  expect(controller.listRepositorySkills).toHaveBeenCalledTimes(1);
  expect(controller.listSkillCenterSkills).toHaveBeenCalledTimes(1);
  expect(controller.listConsumableSpaceSkills).toHaveBeenCalledWith('12', 1, 100);
});

it('工坊可消费 Skill 超过单页时加载全部分页', async () => {
  const firstPage = Array.from({ length: 100 }, (_, index) => ({
    skill_id: `space-${index + 1}`,
    name: `Skill ${index + 1}`,
    latest_published_version: { version: 1 },
  }));
  controller.listConsumableSpaceSkills
    .mockResolvedValueOnce({ data: { total: 101, items: firstPage } })
    .mockResolvedValueOnce({
      data: {
        total: 101,
        items: [{ skill_id: 'space-101', name: 'Skill 101', latest_published_version: { version: 1 } }],
      },
    });

  const result = await botEditorService.loadCapabilityCandidates('bot-1', '12');

  expect(controller.listConsumableSpaceSkills).toHaveBeenNthCalledWith(1, '12', 1, 100);
  expect(controller.listConsumableSpaceSkills).toHaveBeenNthCalledWith(2, '12', 2, 100);
  expect(result.workshopSkills).toHaveLength(101);
});

it('按 SkillCenter、TeamClaw 和工坊可消费接口分类候选 Skill', async () => {
  controller.listRepositorySkills.mockResolvedValue({
    data: { total: 1, items: [{ skill_id: 'repo-1', name: 'TeamClaw Skill' }] },
  });
  controller.listSkillCenterSkills.mockResolvedValue({
    data: { total: 1, items: [{ skillCode: 'sc-1', skillName: 'SkillCenter Skill', latestVersionNumber: 3 }] },
  });
  controller.listConsumableSpaceSkills.mockResolvedValue({
    data: {
      total: 1,
      items: [{ skill_id: 'space-1', name: '工坊 Skill', latest_published_version: { version: 2 } }],
    },
  });

  const result = await botEditorService.loadCapabilityCandidates('bot-1', '12');

  expect(result.marketSkills).toEqual([expect.objectContaining({ id: 'repo-1', source: 'teamclaw-market' })]);
  expect(result.skillCenterSkills).toEqual([
    expect.objectContaining({ id: 'sc-1', source: 'skillcenter-market', version: '3' }),
  ]);
  expect(result.workshopSkills).toEqual([
    expect.objectContaining({ id: 'space-1', source: 'workshop', version: 'V2' }),
  ]);
});

it('通过 Bot OpenAPI 加载并注册副屏 CDN', async () => {
  controller.listRenderScreens.mockResolvedValue({
    data: {
      total: 1,
      items: [{ id: 1, name: 'demo-library', cdn_url: 'https://cdn.example.com/demo.js', creator_id: '149608' }],
    },
  });

  await expect(botEditorService.registerRenderScreenLibraries('bot-1')).resolves.toBe(1);

  expect(controller.listRenderScreens).toHaveBeenCalledWith('bot-1');
  expect(getLibraryCdn('demo-library')).toBe('https://cdn.example.com/demo.js');
});

it('首屏只加载资源根目录，不递归遍历子目录', async () => {
  controller.listResources.mockResolvedValue({
    data: {
      total: 2,
      items: [
        { path: 'config', name: 'config', type: 'folder' },
        { path: '.claude/skills-local', name: 'skills-local', type: 'folder' },
      ],
    },
  });

  const result = await botEditorService.load('bot-1');

  expect(result.resources).toEqual([
    expect.objectContaining({ path: 'config', parentPath: '' }),
    expect.objectContaining({ path: '.claude/skills-local', parentPath: '' }),
  ]);
  expect(controller.listResources).toHaveBeenCalledTimes(1);
  expect(controller.listResources).toHaveBeenCalledWith('bot-1', '');
});

it('按请求目录记录资源父级，不根据资源 path 字符串猜测层级', async () => {
  controller.listResources.mockResolvedValue({
    data: { total: 1, items: [{ path: 'config/deep/file.txt', name: 'file.txt', type: 'file' }] },
  });

  await expect(botEditorService.listResources('bot-1', 'config')).resolves.toEqual([
    expect.objectContaining({ path: 'config/deep/file.txt', parentPath: 'config' }),
  ]);
});

it('上传本地 Skill 目录后只返回我的 Skill，不立即绑定能力集', async () => {
  controller.uploadSkillFolder.mockResolvedValue({
    data: { operation: 'created', skill: { skill_id: 'skill-local', name: '本地 Skill', active: true } },
  });

  await expect(botEditorService.uploadSkillFolder('bot-1', [])).resolves.toEqual(
    expect.objectContaining({ id: 'skill-local', source: 'local' }),
  );
  expect(controller.setSkillSetMcp).not.toHaveBeenCalled();
});

it('添加 MCP 前校验权限，无权限时阻止绑定', async () => {
  controller.getMcpPermission.mockResolvedValue({ data: { has_access: false, tool_permissions: {} } });

  await expect(botEditorService.setSkillSetMcp('bot-1', 'set-1', 'mcp.private', true)).rejects.toThrow(
    '无法添加，请去 MCP 详情页申请权限后重试',
  );
  expect(controller.setSkillSetMcp).not.toHaveBeenCalled();
});

it('MCP 权限通过后再绑定能力集', async () => {
  controller.getMcpPermission.mockResolvedValue({ data: { has_access: true, tool_permissions: {} } });
  controller.setSkillSetMcp.mockResolvedValue({ data: { changed: true } });

  await botEditorService.setSkillSetMcp('bot-1', 'set-1', 'mcp.public', true);

  expect(controller.getMcpPermission).toHaveBeenCalledWith('mcp.public');
  expect(controller.setSkillSetMcp).toHaveBeenCalledWith('bot-1', 'set-1', 'mcp.public', true);
});

it('读取并持久化 MCP caller 模式，不在前端模拟', async () => {
  controller.getCallerContext.mockResolvedValue({
    data: { editable: true, mcp_call_types: { 'mcp.weather': 'caller' }, cli_call_types: {} },
  });
  controller.updateMcpCallType.mockResolvedValue({
    data: { server_code: 'mcp.weather', call_type: 'owner', bot_call_type: 'owner' },
  });

  await expect(botEditorService.getCallerContext('bot-1')).resolves.toEqual({
    editable: true,
    mcpCallTypes: { 'mcp.weather': 'caller' },
    cliCallTypes: {},
  });
  await expect(botEditorService.updateMcpCallType('bot-1', 'mcp.weather', 'owner')).resolves.toBe('owner');
  expect(controller.updateMcpCallType).toHaveBeenCalledWith('bot-1', 'mcp.weather', 'owner');
});
