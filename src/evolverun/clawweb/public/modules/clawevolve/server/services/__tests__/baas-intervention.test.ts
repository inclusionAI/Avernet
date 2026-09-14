import { afterEach, describe, expect, it, vi } from "vitest";

import { sendIntervention } from "../baas-intervention.js";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("sendIntervention", () => {
  it("uses the application API key for a loopback BaaS without requiring an IAM cookie", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { message_id: "message-1", session_id: "session-1" },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await sendIntervention({
      botId: "bot-1",
      sessionKey: "agent:main:session:step-1:user:test",
      sessionId: "session-1",
      message: "/clawevolve cancel",
      transportConfig: {
        apiKey: "baas-key",
        iamtoken: "",
        baseUrl: "http://127.0.0.1:8910",
      },
    });

    expect(result).toEqual({ ok: true, messageId: "message-1", sessionId: "session-1" });
    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect(init?.headers).toMatchObject({ Authorization: "Bearer baas-key" });
    expect(init?.headers).not.toHaveProperty("Cookie");
  });
});
