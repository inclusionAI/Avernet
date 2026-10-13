import { afterEach, describe, expect, it, vi } from "vitest";
import {
  OCB_REPAIR_OPERATION_TYPES,
  OcbRepairGateway,
  parseOcbRepairOperation,
} from "../ocb-gateway.js";
import type { RepairAuthorizationScope } from "../contracts.js";

const ownerScope: RepairAuthorizationScope = {
  actorUserId: "405935",
  ownerId: "405935",
  botId: "20260820_sgv3eody",
  environment: "pre",
};

function gateway(): OcbRepairGateway {
  return new OcbRepairGateway({ baseUrls: { pre: "https://ocb-pre.example.test" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("OcbRepairGateway restart-only boundary", () => {
  it("exposes only the reviewed restart operation", () => {
    expect(OCB_REPAIR_OPERATION_TYPES).toEqual(["restart_bot"]);
    expect(parseOcbRepairOperation({ type: "restart_bot", params: {} })).toEqual({
      type: "restart_bot",
      params: {},
    });
    for (const type of [
      "current_target",
      "engine_config_read",
      "identity_file_read",
      "engine_config_patch",
      "identity_file_replace",
    ]) {
      expect(() => parseOcbRepairOperation({ type, params: {} }))
        .toThrowError(expect.objectContaining({ code: "unsupported_ocb_operation" }));
    }
  });

  it("uses owner identity and the ordinary restart endpoint for a non-admin", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ success: true, data: { ok: true } }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(gateway().execute({
      scope: ownerScope,
      operation: { type: "restart_bot" },
      authHeaders: { cookie: "SESSION=owner", "x-user-id": "405935" },
      callerUserId: "405935",
      callerIsAdmin: false,
    })).resolves.toMatchObject({ operation: "restart_bot", requiresTargetRefresh: true });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe(
      "https://ocb-pre.example.test/api/bots/20260820_sgv3eody/restart?owner_id=405935",
    );
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({});
    expect(init?.headers).toMatchObject({ cookie: "SESSION=owner", "x-user-id": "405935" });
  });

  it("uses administrator identity and restart-for-others for another owner's Bot", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ success: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const scope = { ...ownerScope, actorUserId: "admin-1", ownerId: "405935" };

    await gateway().execute({
      scope,
      operation: { type: "restart_bot" },
      authHeaders: { cookie: "SESSION=admin", "x-user-id": "admin-1" },
      callerUserId: "admin-1",
      callerIsAdmin: true,
    });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe("https://ocb-pre.example.test/api/bots/restart-for-others");
    expect(JSON.parse(String(init?.body))).toEqual({
      target_user_id: "405935",
      target_bot_id: "20260820_sgv3eody",
    });
    expect(init?.headers).toMatchObject({ cookie: "SESSION=admin", "x-user-id": "admin-1" });
  });

  it("does not let a non-admin restart another owner's Bot", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(gateway().execute({
      scope: { ...ownerScope, actorUserId: "other" },
      operation: { type: "restart_bot" },
      authHeaders: { cookie: "SESSION=other", "x-user-id": "other" },
      callerUserId: "other",
      callerIsAdmin: false,
    })).rejects.toMatchObject({ status: 403, code: "repair_owner_scope_required" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requires the verified caller and relayed identity to match", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(gateway().execute({
      scope: ownerScope,
      operation: { type: "restart_bot" },
      authHeaders: { cookie: "SESSION=owner", "x-user-id": "attacker" },
      callerUserId: "405935",
      callerIsAdmin: false,
    })).rejects.toMatchObject({ status: 401, code: "repair_ocb_identity_required" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("preserves a safe OCB business rejection", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      success: false,
      error_code: 403,
      message: "管理员权限不足",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));
    await expect(gateway().execute({
      scope: { ...ownerScope, actorUserId: "admin-1" },
      operation: { type: "restart_bot" },
      authHeaders: { cookie: "SESSION=admin", "x-user-id": "admin-1" },
      callerUserId: "admin-1",
      callerIsAdmin: true,
    })).rejects.toMatchObject({ status: 403, code: "ocb_operation_rejected", message: "管理员权限不足" });
  });
});
