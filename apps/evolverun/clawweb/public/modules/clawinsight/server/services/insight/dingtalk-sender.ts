import { DingTalkNotificationSender, type DingTalkNotificationConfig } from "../notifications/dingtalk-sender.js";
export type DingTalkSenderConfig = DingTalkNotificationConfig;

export type SendImprovementNotificationInput = {
  improvementId: number;
  recipientUserId: string;
  title: string;
  botId: string;
  userGuidance: string | null;
  evidenceCount: number;
  actionType?: "DIRECT_EVOLUTION" | "ASSIGN_OWNER" | null;
};

export class DingTalkSender extends DingTalkNotificationSender {
  async sendImprovementNotification(
    input: SendImprovementNotificationInput,
  ): Promise<{ processQueryKey: string | null }> {
    return this.sendImprovementBatchNotification({
      recipientUserId: input.recipientUserId,
      improvements: [input],
    });
  }

  async sendImprovementBatchNotification(input: {
    recipientUserId: string;
    improvements: SendImprovementNotificationInput[];
  }): Promise<{ processQueryKey: string | null }> {
    if (!this.enabled) {
      throw new Error("DingTalk improvement notification is not configured");
    }
    if (input.improvements.length === 0) {
      throw new Error("DingTalk improvement notification requires at least one improvement");
    }
    const baseUrl = this.config.publicBaseUrl.replace(/\/$/, "");
    const first = input.improvements[0];
    const improvementCount = input.improvements.length;
    const botCount = new Set(input.improvements.map((item) => item.botId)).size;
    const evidenceCount = input.improvements.reduce((sum, item) => sum + item.evidenceCount, 0);
    const autoRepairCount = input.improvements.filter((item) => item.actionType === "DIRECT_EVOLUTION").length;
    const manualRepairCount = improvementCount - autoRepairCount;
    const link = improvementCount === 1
      ? `${baseUrl}/insight?tab=todo&improvementId=${encodeURIComponent(String(first.improvementId))}`
      : `${baseUrl}/insight?tab=todo`;
    const messageTitle = `发现 ${improvementCount} 个问题正在影响你的 Agent 执行成功率`;
    const text = [
      `## ${messageTitle}`,
      "",
      "这些问题已经过管理员确认，请根据修复方式完成授权或手动调整。",
      "",
      `**改进项数量**：${improvementCount} 条`,
      `**涉及 Bot**：${botCount} 个`,
      `**涉及失败任务**：${evidenceCount} 个`,
      `**授权后可自动修复**：${autoRepairCount} 个`,
      `**需要手动修复**：${manualRepairCount} 个`,
      "",
      ...(improvementCount === 1
        ? [
            `**改进项**：${first.title}`,
            `**目标 Bot**：${first.botId}`,
            `**修复方式**：${first.actionType === "DIRECT_EVOLUTION" ? "进入进化室确认授权后自动修复" : "需要你手动调整 Agent 配置"}`,
            "",
            "**改进方向**",
            "",
            first.userGuidance?.trim() || "请结合失败任务证据定位原因，确认改进方案后推进修复。",
          ]
        : [
            "**改进项概览**",
            "",
            ...input.improvements.slice(0, 5).map((item) =>
              `- ${item.title}（Bot：${item.botId}，失败任务：${item.evidenceCount} 个）`,
            ),
            ...(improvementCount > 5 ? [`- 另有 ${improvementCount - 5} 条，请进入改进项列表查看`] : []),
          ]),
      "",
      autoRepairCount > 0
        ? "自动修复会在执行前进入进化室，由你确认方案并授权。"
        : "完成手动修改后，请回到改进项中标记已修复，系统会自动验收。",
      "",
      `[查看并处理改进项](${link})`,
    ].join("\n");
    return this.sendNotification({ recipientUserId: input.recipientUserId, title: messageTitle, text });
  }

}
