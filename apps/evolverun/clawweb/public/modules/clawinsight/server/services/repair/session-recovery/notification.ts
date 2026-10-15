import type { DingTalkNotificationSender } from "../../notifications/dingtalk-sender.js";
import type { RecoveryTaskNotifier } from "./task-service.js";

export class SessionRecoveryNotifier implements RecoveryTaskNotifier {
  constructor(private readonly sender: Pick<DingTalkNotificationSender, "sendNotification"> | null | undefined,
    private readonly publicBaseUrl: string) {}

  async notify(input: Parameters<RecoveryTaskNotifier["notify"]>[0]): Promise<void> {
    if (!this.sender) throw new Error("Notification unavailable");
    const title = input.kind === "plan" ? "会话自愈方案待确认" : "会话自愈需要人工处理";
    const url = new URL("/insight", this.publicBaseUrl);
    url.searchParams.set("module", "monitoring"); url.searchParams.set("recoveryTaskId", input.taskId);
    await this.sender.sendNotification({ recipientUserId: input.ownerId, title,
      text: `${input.summary}\n\n[查看完整方案、证据及任务时间线](${url.href})` });
  }
}
