import { describe, expect, it, vi } from "vitest";
import type { Request, Response } from "express";
import { createEvolveWorkflowActionAccess } from "../evolve-workflow-action-access.js";

function response() {
  return { status: vi.fn().mockReturnThis(), json: vi.fn() } as unknown as Response;
}
const request = {} as Request;

describe("workflow-bound evolve action access", () => {
  it("allows explicit can_view without edit and rejects an unauthorized workflow", async () => {
    const permissions = {
      hasEditPermission: vi.fn(async () => false),
      listBrowserViewableWorkflowIds: vi.fn(async (_actor: string, workflowId?: string) => new Set(workflowId === "wf-view" ? [workflowId] : [])),
    } as never;
    const policy = createEvolveWorkflowActionAccess(permissions, () => "legacy", async () => ({ actorId: "viewer", isAdmin: false }));
    const allowed = response();
    const actor = await policy.actor(request, allowed);
    expect(actor && await policy.require(request, allowed, "wf-view", actor)).toBe(true);
    const denied = response();
    expect(actor && await policy.require(request, denied, "wf-hidden", actor)).toBe(false);
    expect(denied.status).toHaveBeenCalledWith(403);
  });

  it("does not invent a verified principal and preserves legacy edit fallback when no resolver is injected", async () => {
    const hasEditPermission = vi.fn(async () => false);
    const permissions = { hasEditPermission } as never;
    const policy = createEvolveWorkflowActionAccess(permissions, () => "legacy");
    const legacyRequest = { header: (key: string) => key === "X-User-Id" ? "legacy" : undefined, get: () => undefined } as unknown as Request;
    const res = response();
    const actor = await policy.actor(legacyRequest, res);
    expect(actor && await policy.require(legacyRequest, res, "wf-view", actor)).toBe(false);
    expect(hasEditPermission).toHaveBeenCalledWith("wf-view", "legacy");
    expect(res.status).toHaveBeenCalledWith(403);
    const verified = createEvolveWorkflowActionAccess(permissions, () => "legacy", async () => null);
    expect(await verified.actor(legacyRequest, res)).toBeNull();
    expect(res.status).toHaveBeenCalledWith(401);
  });
  it("rejects cross-site JSON and plain form mutations before resolving the principal", async () => {
    const principal = vi.fn(async () => ({ actorId: "viewer", isAdmin: false }));
    const policy = createEvolveWorkflowActionAccess(null, () => null, principal);
    for (const [type, site] of [["application/json", "cross-site"], [false, "same-site"]] as const) {
      const res = response();
      const req = { method: "POST", is: () => type, get: () => site } as unknown as Request;
      expect(await policy.actor(req, res)).toBeNull();
      expect(res.status).toHaveBeenCalledWith(403);
    }
    expect(principal).not.toHaveBeenCalled();
  });
});
