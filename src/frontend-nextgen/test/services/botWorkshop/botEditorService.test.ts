import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { clearCdnConfig, getLibraryCdn } from '@/services/bcs/libraryCdnInjector';
import { botEditorService } from '@/services/botWorkshop/botEditorService';

jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: {
    listSkills: jest.fn(),
    listSkillSets: jest.fn(),
    listBotMcps: jest.fn(),
    listMcpServers: jest.fn(),
    listRepositorySkills: jest.fn(),
    listSpaceSkills: jest.fn(),
    listResources: jest.fn(),
    listRenderScreens: jest.fn(),
    listRoutines: jest.fn(),
    getEngineConfig: jest.fn(),
    getEngineStatus: jest.fn(),
    getApprovalConfig: jest.fn(),
    listSkillSetSkills: jest.fn(),
    listSkillSetMcps: jest.fn(),
    getMcpPermission: jest.fn(),
    setSkillSetMcp: jest.fn(),
    uploadSkillFolder: jest.fn(),
  },
}));

const controller = botEditorController as jest.Mocked<typeof botEditorController>;

beforeEach(() => {
  jest.clearAllMocks();
  clearCdnConfig();
  controller.listSkills.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.listSkillSets.mockResolvedValue({
    data: [{ id: '600005', name: '默认能力集', is_default: true, is_active: true }],
  });
  controller.listBotMcps.mockResolvedValue({ data: [] });
  controller.listMcpServers.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.listRepositorySkills.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.listResources.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.listRenderScreens.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.listRoutines.mockResolvedValue({ data: { total: 0, items: [] } });
  controller.getEngineConfig.mockResolvedValue({ data: {} });
  controller.getEngineStatus.mockResolvedValue({ data: { engine: 'openclaw', active_connections: 0, running: false } });
  controller.listSkillSetSkills.mockRejectedValue(new Error('404 Not found'));
  controller.listSkillSetMcps.mockRejectedValue(new Error('404 Not found'));
});

it('能力集子资源失败时保留能力集并返回降级结果', async () => {
  const result = await botEditorService.load('20260806_wg6wkrk4');

  expect(result.skillSets).toEqual([expect.objectContaining({ id: '600005', skills: [], mcps: [] })]);
  expect(result.errors).toBe(2);
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
