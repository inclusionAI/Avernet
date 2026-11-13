import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { botPublishApprovalService } from '@/services/bots/botPublishApprovalService';

jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: {
    getApprovalConfig: jest.fn(),
    updateApprovalConfig: jest.fn(),
  },
}));

const controller = botEditorController as jest.Mocked<typeof botEditorController>;

describe('botPublishApprovalService 发布审批配置（迁自 botEditorService.test）', () => {
  it('发布审批由通用配置按需加载并透传 owner', async () => {
    controller.getApprovalConfig.mockResolvedValue({ data: { should_approval: true } });

    await expect(botPublishApprovalService.loadApproval('bot-1', 'owner-1')).resolves.toBe(true);

    expect(controller.getApprovalConfig).toHaveBeenCalledWith('bot-1', 'owner-1');
  });

  it('保存审批状态透传 owner 并依赖请求层校验业务码', async () => {
    controller.updateApprovalConfig.mockResolvedValue({ data: { should_approval: false } });

    await botPublishApprovalService.saveApproval('bot-1', false, 'owner-1');

    expect(controller.updateApprovalConfig).toHaveBeenCalledWith('bot-1', false, 'owner-1');
  });
});
