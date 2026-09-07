import { createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ArcaCommandTransport,
  DirectArcaConnectionProvider,
} from "../arca-command-transport.js";

afterEach(() => vi.unstubAllGlobals());

describe("ArcaCommandTransport", () => {
  it("signs a short-lived proxy token directly for the frozen ARCA instance", async () => {
    const getSecretValue = vi.fn(async () => "test-proxy-secret");
    const provider = new DirectArcaConnectionProvider({ getSecretValue }, () => 1_780_000_000);

    const connection = await provider.getConnection({
      environment: "pre",
      bindingId: "1377065",
      sandboxId: "ARCA-SANDBOX-123",
      arcaInstanceId: "ARCA-SANDBOX-123@9",
      ttlSeconds: 120,
      authHeaders: {},
    });

    expect(connection.target).toBe("ARCA_ARCA-SANDBOX-123@9:20003");
    const [encodedHeader, encodedPayload, signature] = connection.token.split(".");
    expect(JSON.parse(Buffer.from(encodedHeader, "base64url").toString("utf8"))).toEqual({
      alg: "HS256",
      typ: "JWT",
    });
    expect(JSON.parse(Buffer.from(encodedPayload, "base64url").toString("utf8"))).toEqual({
      target: connection.target,
      exp: 1_780_000_120,
    });
    expect(signature).toBe(createHmac("sha256", "test-proxy-secret")
      .update(`${encodedHeader}.${encodedPayload}`).digest("base64url"));
    expect(getSecretValue).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(connection)).not.toContain("test-proxy-secret");
  });

  it("rejects an ARCA instance suffix belonging to another frozen sandbox", async () => {
    const provider = new DirectArcaConnectionProvider({ getSecretValue: async () => "secret" });
    await expect(provider.getConnection({
      environment: "pre",
      bindingId: "1377065",
      sandboxId: "ARCA-SANDBOX-123",
      arcaInstanceId: "ARCA-SANDBOX-OTHER@9",
      ttlSeconds: 120,
      authHeaders: {},
    })).rejects.toMatchObject({ code: "repair_arca_target_mismatch" });
  });

  it("uses the locally signed scoped connection token", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      success: true,
      data: { status: "completed", outputs: [], exit_code: 0 },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const connectionProvider = {
      getConnection: vi.fn(async () => ({
        target: "ARCA_ARCA-SANDBOX-123:20003",
        token: "header.payload.signature",
      })),
    };
    const transport = new ArcaCommandTransport({
      connectionProvider,
      proxyBaseUrls: { pre: "https://proxy-pre.example.test" },
      tokenTtlSeconds: 120,
    });

    await expect(transport.execute({
      environment: "pre",
      bindingId: "1377065",
      sandboxId: "ARCA-SANDBOX-123",
      command: "ps -ef",
      authHeaders: { Cookie: "SESSION=owner-secret", "x-user-id": "405935" },
    })).resolves.toMatchObject({ status: "success" });

    expect(connectionProvider.getConnection).toHaveBeenCalledWith({
      environment: "pre",
      bindingId: "1377065",
      sandboxId: "ARCA-SANDBOX-123",
      ttlSeconds: 120,
      authHeaders: { Cookie: "SESSION=owner-secret", "x-user-id": "405935" },
    });
    expect((fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>)["x-proxypass-token"])
      .toBe("header.payload.signature");
  });

  it("forwards only the owner identity plus the short-lived proxy token", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      success: true,
      code: "13-200000",
      message: "success",
      data: {
        status: "completed",
        outputs: [
          { output_type: "stdout", text: "uid=0(root)\n" },
          { output_type: "stderr", text: "" },
        ],
        exit_code: 0,
        execution_time_ms: 17,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const connectionProvider = {
      getConnection: vi.fn(async () => ({
        target: "ARCA_ARCA-SANDBOX-123@9:20003",
        token: "header.payload.signature",
      })),
    };
    const transport = new ArcaCommandTransport({
      connectionProvider,
      proxyBaseUrls: { pre: "https://proxy-pre.example.test" },
      tokenTtlSeconds: 120,
      timeoutSeconds: 30,
    });

    await expect(transport.execute({
      environment: "pre",
      bindingId: "1377065",
      sandboxId: "ARCA-SANDBOX-123@9",
      command: "id",
      authHeaders: { Cookie: "SESSION=owner-secret", "x-user-id": "405935", "x-extra": "drop-me" },
    })).resolves.toEqual({
      status: "success",
      exitCode: 0,
      stdout: "uid=0(root)\n",
      stderr: "",
      durationMs: 17,
    });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe(
      "https://proxy-pre.example.test/proxypass/ARCA_ARCA-SANDBOX-123@9:20003/arca/api/v1/sandbox/ARCA-SANDBOX-123/terminal/exec_command",
    );
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ command: "id" });
    const headers = init?.headers as Record<string, string>;
    expect(headers.Cookie).toBe("SESSION=owner-secret");
    expect(headers["x-user-id"]).toBe("405935");
    expect(headers["x-extra"]).toBeUndefined();
    expect(headers["x-agent-sandbox-id"]).toBe("ARCA-SANDBOX-123");
    expect(headers["x-proxypass-token"]).toBe("header.payload.signature");
    expect(connectionProvider.getConnection).toHaveBeenCalledWith(expect.objectContaining({
      sandboxId: "ARCA-SANDBOX-123",
      bindingId: "1377065",
      authHeaders: { Cookie: "SESSION=owner-secret", "x-user-id": "405935" },
    }));
  });

  it("normalizes login rejection without persisting the returned login URL", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      buserviceErrorCode: "USER_NOT_LOGIN",
      buserviceErrorMsg: "login at https://example.test/?token=must-not-leak",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));
    const transport = new ArcaCommandTransport({
      connectionProvider: {
        getConnection: async () => ({
          target: "ARCA_ARCA-SANDBOX-123:20003",
          token: "header.payload.signature",
        }),
      },
      proxyBaseUrls: { pre: "https://proxy-pre.example.test" },
    });

    await expect(transport.execute({
      environment: "pre",
      bindingId: "1377065",
      sandboxId: "ARCA-SANDBOX-123",
      command: "id",
      authHeaders: { Cookie: "SESSION=owner-secret", "x-user-id": "405935" },
    })).rejects.toMatchObject({
      status: 401,
      code: "repair_arca_identity_required",
      message: "ARCA 运行态访问需要当前 Owner 登录身份",
    });
  });

  it("does not misreport a proxy target rejection as missing owner identity", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      error: "target not authorized",
    }), { status: 403, headers: { "Content-Type": "application/json" } })));
    const transport = new ArcaCommandTransport({
      connectionProvider: {
        getConnection: async () => ({
          target: "ARCA_ARCA-SANDBOX-123:20003",
          token: "header.payload.signature",
        }),
      },
      proxyBaseUrls: { pre: "https://proxy-pre.example.test" },
    });

    await expect(transport.execute({
      environment: "pre",
      bindingId: "1377065",
      sandboxId: "ARCA-SANDBOX-123",
      command: "id",
      authHeaders: { Cookie: "SESSION=owner-secret", "x-user-id": "405935" },
    })).rejects.toMatchObject({
      status: 502,
      code: "repair_arca_proxy_rejected",
      message: "ARCA 代理拒绝了短期连接凭据",
    });
  });

  it("preserves a missing exit code instead of coercing it to zero", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      success: true,
      data: {
        status: "completed",
        outputs: [{ output_type: "stdout", text: "done\n" }],
        exit_code: null,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } })));
    const transport = new ArcaCommandTransport({
      connectionProvider: {
        getConnection: async () => ({
          target: "ARCA_ARCA-SANDBOX-123:20003",
          token: "header.payload.signature",
        }),
      },
      proxyBaseUrls: { pre: "https://proxy-pre.example.test" },
    });

    await expect(transport.execute({
      environment: "pre",
      bindingId: "1377065",
      sandboxId: "ARCA-SANDBOX-123",
      command: "id",
      authHeaders: { Cookie: "SESSION=owner-secret", "x-user-id": "405935" },
    })).resolves.toMatchObject({ exitCode: null, durationMs: null });
  });

  it("rejects a connection token bound to another sandbox", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const transport = new ArcaCommandTransport({
      connectionProvider: {
        getConnection: async () => ({
          target: "ARCA_ARCA-SANDBOX-OTHER:20003",
          token: "header.payload.signature",
        }),
      },
      proxyBaseUrls: { pre: "https://proxy-pre.example.test" },
    });

    await expect(transport.execute({
      environment: "pre",
      bindingId: "1377065",
      sandboxId: "ARCA-SANDBOX-123",
      command: "id",
      authHeaders: { Cookie: "SESSION=owner-secret", "x-user-id": "405935" },
    })).rejects.toMatchObject({ code: "repair_arca_connection_invalid" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
