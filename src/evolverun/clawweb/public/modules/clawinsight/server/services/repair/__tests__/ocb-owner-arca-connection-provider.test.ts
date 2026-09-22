import { afterEach, describe, expect, it, vi } from "vitest";
import { OcbOwnerArcaConnectionProvider } from "../ocb-owner-arca-connection-provider.js";
import { ArcaCommandTransport, DirectArcaConnectionProvider } from "../arca-command-transport.js";
import { createRepairArcaConnectionProvider } from "../arca-transport-factory.js";

const target = { environment: "prod" as const, bindingId: "123", sandboxId: "ARCA-SANDBOX-test",
  arcaInstanceId: "ARCA-SANDBOX-test@0", ttlSeconds: 120 };
const connection = { target: "ARCA_ARCA-SANDBOX-test@0:20003", token: "test.test.test" };
const envelope = { success: true, data: { ...connection, available: true, engine_type: "openclaw" } };
const response = (body: unknown) => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
const identity = { cookie: "IAM_TOKEN=<test>", userId: "test-owner" };
const provider = (getIdentity = () => identity) => new OcbOwnerArcaConnectionProvider({
  baseUrls: { prod: "https://ocb.example.test" }, getIdentity, timeoutMs: 1000,
});
afterEach(() => vi.unstubAllGlobals());

describe("local OCB Owner connection", () => {
  it("exchanges at OCB and relays the same local Owner identity to AgentProxy", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response(envelope)).mockResolvedValueOnce(response({
      success: true, data: { status: "completed", outputs: [{ output_type: "stdout", text: "admin\n" }], exit_code: 0 },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const transport = new ArcaCommandTransport({ connectionProvider: provider(), proxyBaseUrls: { prod: "https://proxy.example.test" } });
    const { ttlSeconds: _, ...input } = target;
    const result = await transport.execute({ ...input, command: "id -un" });
    expect(result).toMatchObject({ status: "success", stdout: "admin\n" });
    expect(JSON.stringify(result)).not.toContain(identity.cookie);
    expect(JSON.stringify(result)).not.toContain(connection.token);
    expect(String(fetchMock.mock.calls[0][0])).toBe("https://ocb.example.test/api/v1/devices/123/connection?port=20003&ttl=120");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "GET", redirect: "manual", headers: { Cookie: identity.cookie, "x-user-id": identity.userId } });
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ redirect: "manual", headers: {
      "x-proxypass-token": connection.token, Cookie: identity.cookie, "x-user-id": identity.userId,
    } });
    expect(fetchMock.mock.calls[1][1].body).toBe(JSON.stringify({ command: "id -un" }));
  });
  it("does not follow an AgentProxy login redirect with Owner credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response(envelope)).mockResolvedValueOnce(new Response(null, {
      status: 302, headers: { Location: "https://login.example.test/?token=<test>" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const transport = new ArcaCommandTransport({ connectionProvider: provider(), proxyBaseUrls: { prod: "https://proxy.example.test" } });
    await expect(transport.execute({ ...target, command: "id" })).rejects.toMatchObject({ code: "repair_ocb_identity_rejected" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1].redirect).toBe("manual");
  });
  it("obtains fresh credentials on every request without a token cache", async () => {
    const getIdentity = vi.fn(() => identity);
    const fetchMock = vi.fn(() => Promise.resolve(response(envelope))); vi.stubGlobal("fetch", fetchMock);
    const instance = provider(getIdentity);
    await instance.getConnection(target); await instance.getConnection(target);
    expect(getIdentity).toHaveBeenCalledTimes(2); expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it.each(["ARCA_ARCA-SANDBOX-other@0:20003", "ARCA_ARCA-SANDBOX-test@1:20003"])("blocks a changed frozen target: %s", async (returnedTarget) => {
    const fetchMock = vi.fn(async () => response({ ...envelope, data: { ...envelope.data, target: returnedTarget } }));
    vi.stubGlobal("fetch", fetchMock);
    const transport = new ArcaCommandTransport({ connectionProvider: provider() });
    await expect(transport.execute({ ...target, command: "id" })).rejects.toBeDefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it.each([302, 401, 403])("rejects expired identity without following HTTP %i", async (status) => {
    const fetchMock = vi.fn(async () => new Response("private-response", { status, headers: { Location: "https://login.example.test/?token=private" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(provider().getConnection(target)).rejects.toMatchObject({ code: "repair_ocb_identity_rejected" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it.each([
    { success: false, message: "private-upstream-error" },
    { success: true, data: { ...envelope.data, available: false } },
    { success: true, data: { ...envelope.data, engine_type: "other" } },
    { success: true, data: { ...envelope.data, token: "not-a-token" } },
  ])("fails closed on invalid connections without leaking the response", async (body) => {
    vi.stubGlobal("fetch", vi.fn(async () => response(body)));
    const error = await provider().getConnection(target).catch(e => e);
    expect(error).toBeInstanceOf(Error);
    expect(error.message).not.toContain("private-upstream-error");
    expect(error.message).not.toContain(connection.token);
  });
  it("normalizes transport exceptions without exposing identity", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw Error(identity.cookie); }));
    await expect(provider().getConnection(target)).rejects.toMatchObject({ code: "repair_ocb_connection_failed" });
  });
  it("rejects an oversized response", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("x".repeat(33 * 1024))));
    await expect(provider().getConnection(target)).rejects.toMatchObject({ code: "repair_ocb_connection_invalid" });
  });
  it("rejects missing identity before making a request", async () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    await expect(provider(() => ({ cookie: "", userId: "" })).getConnection(target)).rejects.toMatchObject({ code: "repair_ocb_identity_required" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it.each([0, 601, 1.5])("rejects invalid TTL %s before fetching", async (ttlSeconds) => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    await expect(provider().getConnection({ ...target, ttlSeconds })).rejects.toMatchObject({ code: "invalid_arca_connection_ttl" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("connection mode composition", () => {
  const config = { publicBaseUrl: "http://127.0.0.1:3002", requestTimeoutMs: 1000 };
  it("retains MIST as the default", () => {
    expect(createRepairArcaConnectionProvider(config, {})).toBeInstanceOf(DirectArcaConnectionProvider);
  });
  it("selects local Owner mode explicitly", () => {
    expect(createRepairArcaConnectionProvider(config, { REPAIR_ARCA_CONNECTION_MODE: "ocb_owner" })).toBeInstanceOf(OcbOwnerArcaConnectionProvider);
  });
  it("does not enable local credentials on a deployed control plane", () => {
    expect(() => createRepairArcaConnectionProvider({ ...config, publicBaseUrl: "https://cw.example.test" }, { REPAIR_ARCA_CONNECTION_MODE: "ocb_owner" })).toThrow("local");
  });
  it("rejects mode typos instead of falling back", () => {
    expect(() => createRepairArcaConnectionProvider(config, { REPAIR_ARCA_CONNECTION_MODE: "ocb_ownre" })).toThrow("Unknown");
  });
});
