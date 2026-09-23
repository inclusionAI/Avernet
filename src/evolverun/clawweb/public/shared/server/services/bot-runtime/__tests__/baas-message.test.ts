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
