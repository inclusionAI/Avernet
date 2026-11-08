// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import type { ResolvedBaasConfig } from "@avernet/clawweb-shared/server/db";
import type { ResolvedMessageInput } from "../contracts.js";
import { BaasRuntimeProvider } from "../providers/baas.js";
const config = { iamtoken: "test-iam", environments: {
  pre: { baseUrl: "https://pre.example.test", apiKey: "test-pre" },
  prod: { baseUrl: "https://prod.example.test", apiKey: "test-prod" },
} } as ResolvedBaasConfig;
const input: ResolvedMessageInput = {
  target: { environment: "pre", provider: "baas", ownerId: "owner", botId: "bot", deviceId: "device",
    bindingId: "binding",
     },
  sessionKey: "agent:main:dashboard:original", deliveryId: "internal-id", deliveryKey: "upstream-key",
  message: "[会话自愈：upstream-key] 继续",
};
afterEach(() => vi.unstubAllGlobals());
it.each(["pre", "prod"] as const)("BaaS %s uses CW messages protocol, preserves session and key, never executes a command", async environment => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({code:0,data:{message_id:"platform-id",session_id:"original-session"}})));
  vi.stubGlobal("fetch",fetch);
  const execute = vi.fn();
  const sender = new BaasRuntimeProvider(config);
  const result = await sender.sendMessage({...input,target:{...input.target,environment}});
  expect(fetch).toHaveBeenCalledTimes(1);
  const [url, request] = fetch.mock.calls[0];
  expect(url).toBe(`https://${environment}.example.test/openapi/v1/messages`);
  expect(request.headers.Authorization).toBe(`Bearer test-${environment}`);
  expect(JSON.parse(request.body)).toEqual({bot_id:"bot:owner",message:input.message,message_id:"upstream-key",
    metadata:{session_id:input.sessionKey,sender_options:{from:"owner"}}});
  expect(execute).not.toHaveBeenCalled();
  expect(result).toMatchObject({status:"accepted",platformMessageId:"platform-id",sessionId:"original-session",locationStatus:"unconfirmed"});
  expect(result.messageId).toBeUndefined();
  expect(JSON.stringify(result)).not.toContain("test-iam");
});
it.each(["network", "empty", "rejected"])("BaaS %s cannot cause retries or container fallback",async variant => {
  const fetch = vi.fn();
  if(variant === "network") fetch.mockRejectedValue(Error("secret-token"));
  else fetch.mockResolvedValue(new Response(JSON.stringify(variant === "empty" ? {} : {code:403}),{status:variant === "rejected" ? 403 : 200}));
  vi.stubGlobal("fetch",fetch);
  const execute=vi.fn();
  const result=await new BaasRuntimeProvider(config).sendMessage(input);
  expect(result.status).toBe(variant === "rejected" ? "failed" : "unknown");
  expect(fetch).toHaveBeenCalledTimes(1);expect(execute).not.toHaveBeenCalled();
  expect(JSON.stringify(result)).not.toContain("secret-token");
});

it("routes a diagnosed instance message through the existing Adapter worker on that exact device", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, data: { exit_code: 0,
    stdout: JSON.stringify({ status: "accepted", sessionId: "original", locationStatus: "unconfirmed" }) } })));
  vi.stubGlobal("fetch", fetch);
  const target = { ...input.target, activeEngine: "openclaw", deviceUuid: "DEVICE-" + "a".repeat(32),
    providerDeviceId: "ARCA-SANDBOX-exact@0", runtimeIdentity: { stage: "online" as const, version: "V16" } };
  const prepare = vi.fn(command => command);
  expect(await new BaasRuntimeProvider(config, { prepareMessageCommand: prepare }).sendMessage({ ...input, target })).toMatchObject({ status: "accepted", sessionId: "original" });
  expect(fetch).toHaveBeenCalledOnce();
  expect(String(fetch.mock.calls[0][0])).toBe("https://pre.example.test/api/v1/paas/devices/ARCA-SANDBOX-exact%400/commands");
  expect(prepare).toHaveBeenCalledOnce();
  const command = JSON.parse(fetch.mock.calls[0][1].body).cmd;
  const encoded = /'([^']+)'$/.exec(command)![1];
  const worker = JSON.parse(Buffer.from(encoded, "base64").toString());
  expect(worker).toMatchObject({ sessionKey: input.sessionKey, deliveryKey: input.deliveryKey,
    message: input.message, engineUrl: "ws://127.0.0.1:20003/ws", engineAuthToken: "OPEN_API:app:test-pre" });
  expect(worker.createSession).toBe(false);
});
it("keeps TE on BaaS messages and forwards its service stage without Shell", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, data: { message_id: "message" } })));
  vi.stubGlobal("fetch", fetch);
  await new BaasRuntimeProvider(config).sendMessage({ ...input, target: { ...input.target, activeEngine: "teclaw",
    runtimeIdentity: { stage: "verify" } } });
  expect(fetch).toHaveBeenCalledOnce();
  expect(fetch.mock.calls[0][0]).toBe("https://pre.example.test/openapi/v1/messages");
  expect(JSON.parse(fetch.mock.calls[0][1].body).metadata).toMatchObject({ session_id: input.sessionKey, bot_options: { lifecycle_stage: "verify" } });
});
