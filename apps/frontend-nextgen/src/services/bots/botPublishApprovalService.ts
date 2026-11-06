import { botEditorController } from '@/services/backendApi/bots/botEditorController';

// 发布审批配置读写：自 services/botWorkshop/botEditorService 迁入
// （外部 Bot 通用配置弹窗在 botWorkshop 禁入前缀外复用，见 collab-form spec §4）。
export const botPublishApprovalService = {
  async loadApproval(botId: string, ownerId?: string) {
    const response = await botEditorController.getApprovalConfig(botId, ownerId);
    return Boolean(response.data?.should_approval);
  },
  saveApproval: (botId: string, enabled: boolean, ownerId?: string) =>
    botEditorController.updateApprovalConfig(botId, enabled, ownerId),
};
