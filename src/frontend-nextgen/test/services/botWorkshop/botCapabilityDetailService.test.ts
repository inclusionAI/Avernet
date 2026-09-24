import { botCapabilityDetailController as api } from '@/services/backendApi/bots/botCapabilityDetailController';
import { botCapabilityDetailService } from '@/services/botWorkshop/botCapabilityDetailService';

jest.mock('@/services/backendApi/bots/botCapabilityDetailController', () => ({
  botCapabilityDetailController: { skill: jest.fn(), skillContent: jest.fn(), mcp: jest.fn() },
}));

test('Skill 详情读取当前 Bot 的正文并传递真实 Owner', async () => {
  (api.skill as jest.Mock).mockResolvedValue({ code: 200000, data: { name: '报告', description: '生成报告' } });
  (api.skillContent as jest.Mock).mockResolvedValue({ code: 200000, data: { content: '# 使用说明' } });
  const detail = await botCapabilityDetailService.load('bot-1', { kind: 'skill', id: '42', name: '报告' }, 'owner-1');
  expect(api.skillContent).toHaveBeenCalledWith('bot-1', '42', 'owner-1');
  expect(detail.content).toBe('# 使用说明');
});

test('MCP 详情将文档和工具参数映射为展示数据', async () => {
  (api.mcp as jest.Mock).mockResolvedValue({
    code: 200000,
    data: {
      name: '搜索',
      docs: { overview: '# 搜索说明' },
      tools: [{ name: 'search', description: '搜索文档', inputSchema: { type: 'object' } }],
    },
  });
  const detail = await botCapabilityDetailService.load('bot-1', { kind: 'mcp', id: 'search', name: '搜索' });
  expect(detail.content).toBe('# 搜索说明');
  expect(detail.tools[0]).toEqual({ name: 'search', description: '搜索文档', parameters: '{\n  "type": "object"\n}' });
});

test('后端业务错误透传给详情错误态', async () => {
  (api.mcp as jest.Mock).mockResolvedValue({ code: 404000, message: 'MCP 不存在', data: null });
  await expect(botCapabilityDetailService.load('bot-1', { kind: 'mcp', id: 'missing', name: 'MCP' })).rejects.toThrow(
    'MCP 不存在',
  );
});
