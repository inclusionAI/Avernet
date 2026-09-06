import { afterEach, describe, expect, it, vi } from "vitest";
import { DingTalkSender } from "../dingtalk-sender.js";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("DingTalkSender", () => {
  it("gets a token and sends a Markdown one-to-one improvement message", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = vi.fn(async (input, init) => {
      calls.push({ url: String(input), init });
      if (String(input).endsWith("/oauth2/accessToken")) {
        return new Response(JSON.stringify({ accessToken: "access-token", expireIn: 7200 }), { status: 200 });
      }
      return new Response(JSON.stringify({ processQueryKey: "process-1" }), { status: 200 });
    }) as typeof fetch;

    const sender = new DingTalkSender({
      appKey: "app-key",
      appSecret: "app-secret",
      robotCode: "robot-code",
      apiBaseUrl: "https://api.dingtalk.test",
      publicBaseUrl: "https://clawweb-pre.test",
    });
    const result = await sender.sendImprovementNotification({
      improvementId: 123,
      recipientUserId: "205357",
      title: "补齐环境配置",
      botId: "bot-1",
      userGuidance: "请优先检查环境变量。",
      evidenceCount: 3,
      actionType: "DIRECT_EVOLUTION",
    });

    expect(result).toEqual({ processQueryKey: "process-1" });
    expect(calls).toHaveLength(2);
    expect(calls[0].url).toBe("https://api.dingtalk.test/v1.0/oauth2/accessToken");
    expect(calls[1].url).toBe("https://api.dingtalk.test/v1.0/robot/oToMessages/batchSend");
    const sendBody = JSON.parse(String(calls[1].init?.body)) as Record<string, unknown>;
    expect(sendBody).toEqual(expect.objectContaining({
      robotCode: "robot-code",
      userIds: ["205357"],
      msgKey: "sampleMarkdown",
    }));
    const message = JSON.parse(String(sendBody.msgParam)) as { title: string; text: string };
    expect(message.title).toBe("发现 1 个问题正在影响你的 Agent 执行成功率");
    expect(message.text).toContain("这些问题已经过管理员确认");
    expect(message.text).not.toContain("ClawWeb");
    expect(message.text).toContain("**改进项**：补齐环境配置");
    expect(message.text).toContain("**目标 Bot**：bot-1");
    expect(message.text).toContain("**涉及失败任务**：3 个");
    expect(message.text).toContain("**授权后可自动修复**：1 个");
    expect(message.text).toContain("**需要手动修复**：0 个");
    expect(message.text).toContain("**修复方式**：进入进化室确认授权后自动修复");
    expect(message.text).toContain("**改进方向**");
    expect(message.text).toContain("请优先检查环境变量。");
    expect(message.text).toContain("自动修复会在执行前进入进化室，由你确认方案并授权。");
    expect(message.text).toContain("[查看并处理改进项](https://clawweb-pre.test/insight?tab=todo&improvementId=123)");
  });
  it("sends one summary message for multiple improvements assigned to the same recipient", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = vi.fn(async (input, init) => {
      calls.push({ url: String(input), init });
      if (String(input).endsWith("/oauth2/accessToken")) {
        return new Response(JSON.stringify({ accessToken: "access-token", expireIn: 7200 }), { status: 200 });
      }
      return new Response(JSON.stringify({ processQueryKey: "process-batch" }), { status: 200 });
    }) as typeof fetch;

    const sender = new DingTalkSender({
      appKey: "app-key",
      appSecret: "app-secret",
      robotCode: "robot-code",
      apiBaseUrl: "https://api.dingtalk.test",
      publicBaseUrl: "https://embedded-app.test",
    });
    await sender.sendImprovementBatchNotification({
      recipientUserId: "205357",
      improvements: [
        { improvementId: 1, recipientUserId: "205357", title: "Bot A · 工具失败改进", botId: "bot-a", userGuidance: null, evidenceCount: 3, actionType: "DIRECT_EVOLUTION" },
        { improvementId: 2, recipientUserId: "205357", title: "Bot B · 执行失败改进", botId: "bot-b", userGuidance: null, evidenceCount: 4, actionType: "ASSIGN_OWNER" },
      ],
    });

    expect(calls.filter((call) => call.url.endsWith("/robot/oToMessages/batchSend"))).toHaveLength(1);
    const sendCall = calls.find((call) => call.url.endsWith("/robot/oToMessages/batchSend"));
    const sendBody = JSON.parse(String(sendCall?.init?.body)) as Record<string, unknown>;
    const message = JSON.parse(String(sendBody.msgParam)) as { title: string; text: string };
    expect(message.title).toBe("发现 2 个问题正在影响你的 Agent 执行成功率");
    expect(message.text).toContain("**改进项数量**：2 条");
    expect(message.text).toContain("**涉及 Bot**：2 个");
    expect(message.text).toContain("**涉及失败任务**：7 个");
    expect(message.text).toContain("**授权后可自动修复**：1 个");
    expect(message.text).toContain("**需要手动修复**：1 个");
    expect(message.text).toContain("自动修复会在执行前进入进化室，由你确认方案并授权。");
    expect(message.text).not.toContain("ClawWeb");
    expect(message.text).toContain("[查看并处理改进项](https://embedded-app.test/insight?tab=todo)");
  });

  it("pads a five-digit numeric staff id exactly once", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = vi.fn(async (input, init) => {
      calls.push({ url: String(input), init });
      if (String(input).endsWith("/oauth2/accessToken")) {
        return new Response(JSON.stringify({ accessToken: "access-token", expireIn: 7200 }), { status: 200 });
      }
      return new Response(JSON.stringify({ processQueryKey: "process-five-digit" }), { status: 200 });
    }) as typeof fetch;
    const sender = new DingTalkSender({
      appKey: "app-key",
      appSecret: "app-secret",
      robotCode: "robot-code",
      apiBaseUrl: "https://api.dingtalk.test",
      publicBaseUrl: "https://clawweb-pre.test",
    });
    await sender.sendImprovementNotification({
      improvementId: 1,
      recipientUserId: "12345",
      title: "测试",
      botId: "bot-1",
      userGuidance: null,
      evidenceCount: 1,
    });
    const sendCall = calls.find((call) => call.url.endsWith("/robot/oToMessages/batchSend"));
    const body = JSON.parse(String(sendCall?.init?.body)) as { userIds: string[] };
    expect(body.userIds).toEqual(["012345"]);
  });

  it("keeps the underlying network error code without exposing request credentials", async () => {
    const networkError = Object.assign(new Error("unable to verify the first certificate"), {
      code: "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
    });
    globalThis.fetch = vi.fn(async () => {
      throw Object.assign(new TypeError("fetch failed"), { cause: networkError });
    }) as typeof fetch;

    const sender = new DingTalkSender({
      appKey: "app-key",
      appSecret: "app-secret",
      robotCode: "robot-code",
      apiBaseUrl: "https://api.dingtalk.test",
      publicBaseUrl: "https://clawweb-pre.test",
    });

    await expect(sender.sendImprovementNotification({
      improvementId: 123,
      recipientUserId: "205357",
      title: "测试改进项",
      botId: "bot-1",
      userGuidance: null,
      evidenceCount: 1,
    })).rejects.toThrow(
      "DingTalk access token request failed: fetch failed (UNABLE_TO_VERIFY_LEAF_SIGNATURE: unable to verify the first certificate)",
    );
  });

});
