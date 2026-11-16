// @vitest-environment node
import { createServer } from "node:http";
import { afterEach, expect, it, vi } from "vitest";
import { DingTalkCardNotDispatchedError, DingTalkNotificationSender } from "../dingtalk-sender.js";
const servers: ReturnType<typeof createServer>[] = [];
afterEach(async () => { await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => server.close(() => resolve())))); });
it("reuses authenticated token delivery for an Owner native card and updates the same instance", async () => {
  const calls: Array<{ method: string; url: string; token: string | undefined; body: Record<string, unknown> }> = [];
  const server = createServer(async (req, res) => {
    let raw = ""; for await (const chunk of req) raw += chunk;
    calls.push({ method: req.method!, url: req.url!, token: req.headers["x-acs-dingtalk-access-token"] as string | undefined, body: JSON.parse(raw) });
    res.setHeader("content-type", "application/json"); res.end(JSON.stringify(req.url?.endsWith("accessToken") ? { accessToken: "fixture", expireIn: 7200 } : { success: true, result: { deliverResults: [{ success: true }] } }));
  }); servers.push(server);
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as { port: number };
  const sender = new DingTalkNotificationSender({ appKey: "app", appSecret: "secret", robotCode: "robot", apiBaseUrl: `http://127.0.0.1:${address.port}`, publicBaseUrl: "https://cw.invalid" });
  const config = JSON.stringify({ autoLayout: true });
  await sender.sendCard({ recipientUserId: "123", outTrackId: "owner-pre-task", templateId: "template", callbackRouteKey: "pre-route", cardParamMap: { instructions: "请补图1", config, confirm_status: "normal" } });
  await sender.updateCard({ outTrackId: "owner-pre-task", cardParamMap: { status: "核验中", config, confirm_status: "disabled" } });
  expect(calls.map(call => [call.method, call.url])).toEqual([["POST", "/v1.0/oauth2/accessToken"], ["POST", "/v1.0/card/instances/createAndDeliver"], ["PUT", "/v1.0/card/instances"]]);
  expect(calls[1]).toMatchObject({ token: "fixture", body: { outTrackId: "owner-pre-task", callbackType: "HTTP", callbackRouteKey: "pre-route", cardTemplateId: "template", openSpaceId: "dtv1.card//IM_ROBOT.000123", imRobotOpenDeliverModel: { robotCode: "robot" }, cardData: { cardParamMap: { instructions: "请补图1", config, confirm_status: "normal" } } } });
  expect(calls[2]).toMatchObject({ token: "fixture", body: { outTrackId: "owner-pre-task", cardData: { cardParamMap: { status: "核验中", config, confirm_status: "disabled" } } } });
  expect(calls[1].body).not.toHaveProperty("config");
  expect(calls[2].body).not.toHaveProperty("config");
  await sender.sendCard({ recipientUserId: "123", outTrackId: "handoff-pre-task", templateId: "template",
    cardParamMap: { instructions: "请回原会话补材料", confirm_visible: "false", confirm_status: "disabled", config } });
  expect(calls).toHaveLength(4);
  expect(calls[3]).toMatchObject({ token: "fixture", method: "POST", url: "/v1.0/card/instances/createAndDeliver",
    body: { outTrackId: "handoff-pre-task", openSpaceId: "dtv1.card//IM_ROBOT.000123" } });
  expect(calls[3].body).not.toHaveProperty("callbackType");
  expect(calls[3].body).not.toHaveProperty("callbackRouteKey");
});

it("distinguishes preparation failure from a lost response after sending the card request", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response("{}", { status: 503 }))
    .mockResolvedValueOnce(Response.json({ accessToken: "fixture", expireIn: 7200 }))
    .mockRejectedValueOnce(new Error("response lost"));
  vi.stubGlobal("fetch", fetch);
  try {
    const sender = new DingTalkNotificationSender({ appKey: "app", appSecret: "secret", robotCode: "robot",
      apiBaseUrl: "https://platform.invalid", publicBaseUrl: "https://cw.invalid" });
    const card = { recipientUserId: "123", outTrackId: "owner-pre-task", templateId: "template", cardParamMap: {} };
    await expect(sender.sendCard(card)).rejects.toBeInstanceOf(DingTalkCardNotDispatchedError);
    expect(fetch).toHaveBeenCalledTimes(1);
    const lost = sender.sendCard(card);
    await expect(lost).rejects.toThrow("response lost");
    await expect(lost).rejects.not.toBeInstanceOf(DingTalkCardNotDispatchedError);
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      "https://platform.invalid/v1.0/oauth2/accessToken",
      "https://platform.invalid/v1.0/oauth2/accessToken",
      "https://platform.invalid/v1.0/card/instances/createAndDeliver",
    ]);
  } finally {
    vi.unstubAllGlobals();
  }
});
