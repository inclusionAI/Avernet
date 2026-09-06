export type DingTalkSenderConfig = {
  appKey: string;
  appSecret: string;
  robotCode: string;
  apiBaseUrl: string;
  publicBaseUrl: string;
};

type DingTalkTokenResponse = {
  accessToken?: string;
  access_token?: string;
  expireIn?: number;
  expires_in?: number;
};

function describeNetworkError(error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  const cause = error.cause;
  if (!cause || typeof cause !== "object") return error.message;
  const code = "code" in cause ? String(cause.code) : "";
  const message = "message" in cause ? String(cause.message) : "";
  const detail = [code, message].filter(Boolean).join(": ");
  return detail ? `${error.message} (${detail})` : error.message;
}

export type SendImprovementNotificationInput = {
  improvementId: number;
  recipientUserId: string;
  title: string;
  botId: string;
  userGuidance: string | null;
  evidenceCount: number;
  actionType?: "DIRECT_EVOLUTION" | "ASSIGN_OWNER" | null;
};

export class DingTalkSender {
  private cachedToken: { token: string; expiresAt: number } | null = null;

  constructor(private readonly config: DingTalkSenderConfig) {}

  get enabled(): boolean {
    return Boolean(
      this.config.appKey
      && this.config.appSecret
      && this.config.robotCode
      && this.config.publicBaseUrl,
    );
  }

  private async getAccessToken(): Promise<string> {
    if (this.cachedToken && this.cachedToken.expiresAt > Date.now() + 60_000) {
      return this.cachedToken.token;
    }

    let response: Response;
    try {
      response = await fetch(`${this.config.apiBaseUrl}/v1.0/oauth2/accessToken`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          appKey: this.config.appKey,
          appSecret: this.config.appSecret,
        }),
      });
    } catch (error) {
      throw new Error(`DingTalk access token request failed: ${describeNetworkError(error)}`, { cause: error });
    }
    const body = await response.json() as DingTalkTokenResponse & Record<string, unknown>;
    if (!response.ok) {
      throw new Error(`DingTalk access token HTTP ${response.status}`);
    }
    const token = body.accessToken ?? body.access_token;
    if (!token) throw new Error("DingTalk access token missing");
    const expireIn = Number(body.expireIn ?? body.expires_in ?? 7200);
    this.cachedToken = {
      token,
      expiresAt: Date.now() + Math.max(60, expireIn) * 1000,
    };
    return token;
  }

  private normalizeUserId(userId: string): string {
    const normalized = userId.trim();
    return /^\d+$/.test(normalized) ? normalized.padStart(6, "0") : normalized;
  }

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
    const accessToken = await this.getAccessToken();
    const recipientUserId = this.normalizeUserId(input.recipientUserId);
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
    let response: Response;
    try {
      response = await fetch(`${this.config.apiBaseUrl}/v1.0/robot/oToMessages/batchSend`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-acs-dingtalk-access-token": accessToken,
        },
        body: JSON.stringify({
          robotCode: this.config.robotCode,
          userIds: [recipientUserId],
          msgKey: "sampleMarkdown",
          msgParam: JSON.stringify({ title: messageTitle, text }),
        }),
      });
    } catch (error) {
      throw new Error(`DingTalk send message request failed: ${describeNetworkError(error)}`, { cause: error });
    }
    const body = await response.json() as Record<string, unknown>;
    if (!response.ok) {
      throw new Error(`DingTalk send message HTTP ${response.status}`);
    }
    if (body.success === false && !body.processQueryKey) {
      throw new Error(`DingTalk send message rejected: ${String(body.errmsg ?? body.message ?? "unknown error")}`);
    }
    return { processQueryKey: typeof body.processQueryKey === "string" ? body.processQueryKey : null };
  }

}
