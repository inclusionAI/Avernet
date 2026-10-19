import { afterEach, describe, expect, it, vi } from "vitest";

import { sendIntervention, type InterventionParams } from "../baas-intervention.js";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("sendIntervention", () => {
  it("uses the application API key for a loopback BaaS without requiring an IAM cookie", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
      code: 0,
      data: { message_id: "message-1", session_id: "session-1" },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await sendIntervention({
      // BaaS requires the "<bot_id>:<owner_staff_no>" form.
      botId: "bot-1:123456",
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

  it.each([
    ["20260624_32glp1po", "a bare bot id"],
    ["", "an empty bot id"],
    ["bot-1:", "a missing owner staff number"],
    [":123456", "a missing bot id"],
  ])("rejects %s (%s) before calling BaaS", async (botId) => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    const result = await sendIntervention({ ...params, botId });
    expect(result.ok).toBe(false);
    expect(result.error).toContain("bot_id 格式非法");
    // BaaS answers a bare id with a detail-free 400; fail fast instead.
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

const params: InterventionParams = {
  botId: "default:123456",
  sessionKey: "agent:main:session:step-1:user:test",
  sessionId: "session-1",
  message: "/clawevolve cancel",
  transportConfig: {
    apiKey: "<fixture-api-key>",
    iamtoken: "fixture-iam.$[token]+",
    baseUrl: "http://127.0.0.1:8910",
  },
};

function mockResponse(payload: unknown, status = 400) {
  const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("sendIntervention HTTP error details", () => {
  it("reports the real nested 60001 binding error and trace without retrying HTTP 400", async () => {
    const fetchMock = mockResponse({
      detail: { code: 60001, message: "Bot binding not found: default:123456" },
      trace_id: "trace-binding-1",
    });
    const result = await sendIntervention(params);
    expect(result.ok).toBe(false);
    expect(result.error).toBe(
      "BaaS API 错误: 400 60001 Bot binding not found: default:123456 (trace_id=trace-binding-1)",
    );
    expect(result.tokenExpired).not.toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("uses a string detail without exposing unrelated response fields", async () => {
    mockResponse({
      detail: "Session not exist",
      headers: { Authorization: "header-only-secret" },
      body: "body-only-private-text",
      diagnostic: "diagnostic-only-private-text",
    });
    expect(await sendIntervention(params)).toEqual({
      ok: false,
      error: "BaaS API 错误: 400 Session not exist",
    });
  });

  it("renders only loc and msg from HTTP 422 validation errors", async () => {
    const fetchMock = mockResponse({
      detail: [
        {
          loc: ["body", "message"], msg: "Field required", type: "private-type",
          input: { message: "private-input", apiKey: "<private-input-key>" },
          ctx: { error: "private-context" },
        },
        { loc: ["body", "messages", 0], msg: "String required", input: "private-body" },
        null,
        { loc: ["body", "ignored"], msg: { input: "private-malformed-message" } },
      ],
    }, 422);
    const result = await sendIntervention(params);
    expect(result).toEqual({
      ok: false,
      error: "BaaS API 错误: 422 body.message: Field required; body.messages.0: String required",
    });
    expect(result.error).not.toContain("private-");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    [{ buserviceErrorCode: "BUSINESS_ERROR", buserviceErrorMsg: "Legacy failure", message: "fallback" }, "BUSINESS_ERROR Legacy failure"],
    [{ buserviceErrorCode: "BUSINESS_ERROR", message: "Legacy fallback" }, "BUSINESS_ERROR Legacy fallback"],
    [{ code: 60001, message: "Top-level failure" }, "60001 Top-level failure"],
    [{ message: "Message only" }, "Message only"],
    [{ detail: { error_code: "INVALID_REQUEST", message: "Invalid request" } }, "INVALID_REQUEST Invalid request"],
    [{ buserviceErrorCode: "", buserviceErrorMsg: "", message: " ", detail: { code: 60001, message: "Nested fallback" } }, "60001 Nested fallback"],
  ])("preserves existing envelopes and supported detail fields: %j", async (payload, expected) => {
    mockResponse(payload);
    expect((await sendIntervention(params)).error).toBe(`BaaS API 错误: 400 ${expected}`);
  });

  it.each(["TOKEN_EXPIRED", "TOKEN_INVALID", "USER_NOT_LOGIN"])(
    "keeps the existing %s tokenExpired decision ahead of success and retry logic",
    async (code) => {
      const fetchMock = mockResponse({
        buserviceErrorCode: code,
        buserviceErrorMsg: `Expired ${params.transportConfig!.iamtoken}`,
        trace_id: "trace-auth-1",
        data: { message_id: "must-not-succeed" },
      }, 200);
      const result = await sendIntervention(params);
      expect(result).toMatchObject({ ok: false, tokenExpired: true });
      expect(result.error).toContain("IAM Token 已过期或无效");
      expect(result.error).toContain("200");
      expect(result.error).toContain(code);
      expect(result.error).toContain("Expired");
      expect(result.error).toContain("trace-auth-1");
      expect(result.error).not.toContain(params.transportConfig!.iamtoken);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it("does not expand tokenExpired detection to nested detail codes", async () => {
    const fetchMock = mockResponse({ detail: { code: "TOKEN_EXPIRED", message: "Nested failure" } }, 401);
    const result = await sendIntervention(params);
    expect(result.tokenExpired).not.toBe(true);
    expect(result.error).toBe("BaaS API 错误: 401 TOKEN_EXPIRED Nested failure");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    null, "private-primitive", 7, false, [],
    { detail: null }, { detail: 42 },
    { detail: { code: { private: "private-code" }, message: ["private-message"] }, trace_id: { private: "private-trace" } },
    { buserviceErrorCode: { private: "private-code" }, buserviceErrorMsg: ["private-message"], message: true },
  ].map((payload) => ({ payload })))("handles malformed JSON envelope $payload without a network error or HTTP 400 retry", async ({ payload }) => {
    vi.useFakeTimers();
    const fetchMock = mockResponse(payload);
    const pending = sendIntervention(params);
    await vi.runAllTimersAsync();
    expect(await pending).toEqual({ ok: false, error: "BaaS API 错误: 400" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("describes a non-JSON body instead of reporting an unattributable 400", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response("<html>gateway rejected the call</html>", {
      status: 400,
      headers: { "Content-Type": "text/html" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await sendIntervention(params);
    // A bare "400" gave no way to tell a gateway rejection from a BaaS error.
    expect(result.ok).toBe(false);
    expect(result.error).toContain("BaaS API 错误: 400");
    expect(result.error).toContain("text/html");
    expect(result.error).toContain("gateway rejected the call");
    // Still a client error: parsing failure must not be retried as a network fault.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("never echoes a JSON body, so a validation envelope cannot leak the request", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
      detail: [{ loc: ["body", "bot_id"], msg: "field required" }],
      private_echo: "should-not-appear",
    }), { status: 422, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await sendIntervention(params);
    expect(result.error).toContain("body.bot_id: field required");
    expect(result.error).not.toContain("should-not-appear");
  });

  it.each(["nested", "legacy", "validation"])("redacts known credential literals from all allowed %s fields", async (shape) => {
    const { apiKey, iamtoken } = params.transportConfig!;
    const message = `Rejected ${apiKey} / ${iamtoken} / ${apiKey}; Authorization: Bearer unrelated-secret`;
    const payload = shape === "nested"
      ? { detail: { code: apiKey, message } }
      : shape === "legacy"
        ? { buserviceErrorCode: apiKey, buserviceErrorMsg: message }
        : { detail: [{ loc: ["body", apiKey, { input: "private-location" }], msg: message, input: "private-input" }] };
    mockResponse({ ...payload, trace_id: iamtoken });
    const result = await sendIntervention(params);
    expect(result.error).toContain("Rejected");
    expect(result.error).toContain("[REDACTED]");
    for (const secret of [apiKey, iamtoken, "unrelated-secret", "private-location", "private-input"]) {
      expect(result.error).not.toContain(secret);
    }
  });

  it("bounds error output to about 1KB in UTF-8, after redacting secrets", async () => {
    const apiKey = `${"x".repeat(1100)}secret-end`;
    mockResponse({ detail: `Rejected ${apiKey} ${"错误".repeat(2000)}`, trace_id: "trace-long" });
    const result = await sendIntervention({ ...params, transportConfig: { ...params.transportConfig!, apiKey } });
    expect(result.error).toContain("Rejected [REDACTED]");
    expect(result.error).toContain("[TRUNCATED]");
    expect(result.error).not.toContain("x".repeat(10));
    expect(Buffer.byteLength(result.error!, "utf8")).toBeLessThanOrEqual(1024);
  });

  it.each([429, 503])("keeps three attempts with 2s/4s backoff for HTTP %i and reports safe details", async (status) => {
    vi.useFakeTimers();
    const fetchMock = mockResponse({ detail: { code: 60001, message: "Temporarily unavailable" } }, status);
    const pending = sendIntervention(params);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(3999);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    const result = await pending;
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(result.error).toContain(String(status));
    expect(result.error).toContain("60001");
    expect(result.error).toContain("Temporarily unavailable");
    expect(result.error).toContain("attempt 3/3");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps retries for genuine network failures and redacts their messages", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn<typeof fetch>().mockRejectedValue(new Error(`Connection failed ${params.transportConfig!.apiKey}`));
    vi.stubGlobal("fetch", fetchMock);
    const pending = sendIntervention(params);
    await vi.runAllTimersAsync();
    const result = await pending;
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(result.error).toContain("BaaS 网络错误: Connection failed");
    expect(result.error).toContain("attempt 3/3");
    expect(result.error).not.toContain(params.transportConfig!.apiKey);
  });
});

describe("sendIntervention request metadata", () => {
  it.each([undefined, "draft", "verify", "online"] as const)(
    "preserves the target and emits lifecycle metadata only for the explicit %s stage",
    async (lifecycleStage) => {
      const fetchMock = mockResponse({ code: 0, data: { message_id: "message-1", session_id: "session-1" } }, 200);
      const result = await sendIntervention({ ...params, ...(lifecycleStage ? { lifecycleStage } : {}) });
      expect(result).toEqual({ ok: true, messageId: "message-1", sessionId: "session-1" });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe(`${params.transportConfig!.baseUrl}/openapi/v1/messages`);
      expect(init?.method).toBe("POST");
      expect(init?.headers).toEqual({ "Content-Type": "application/json", Authorization: `Bearer ${params.transportConfig!.apiKey}` });
      expect(JSON.parse(init!.body as string)).toEqual({
        bot_id: params.botId,
        message: params.message,
        metadata: {
          session_id: params.sessionId,
          ...(lifecycleStage ? { bot_options: { lifecycle_stage: lifecycleStage } } : {}),
          sender_options: { from: "owner" },
        },
      });
    },
  );

  it("omits session_id so a caller without a session starts a new one", async () => {
    const fetchMock = mockResponse({ data: { message_id: "message-1" } }, 200);
    await sendIntervention({ ...params, sessionKey: "auto-heal-run:wf:1", sessionId: null });
    const [, init] = fetchMock.mock.calls[0]!;
    // A synthesized key is not a real session. Sending it as session_id targeted
    // a session that never existed, so a new session is opened instead.
    expect(JSON.parse(init!.body as string).metadata).toEqual({
      sender_options: { from: "owner" },
    });
  });

  it("still addresses an existing session when one is resolved", async () => {
    const fetchMock = mockResponse({ data: { message_id: "message-1" } }, 200);
    await sendIntervention({ ...params, sessionId: "99dc428b-3ce7-4c4d-bd63-a9885a24719d" });
    const [, init] = fetchMock.mock.calls[0]!;
    expect(JSON.parse(init!.body as string).metadata).toMatchObject({
      session_id: "99dc428b-3ce7-4c4d-bd63-a9885a24719d",
    });
  });
});
