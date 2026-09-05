import { describe, expect, it, vi } from "vitest";
import type { EvolveRepository } from "../../../repositories/evolve-repository.js";
import { RepositoryRepairTargetResolver } from "../repository-target-resolver.js";

function resolverWith(runtime: Record<string, unknown> | null) {
  const resolveEvolveBotRuntimeForOwner = vi.fn(async () => runtime);
  const resolver = new RepositoryRepairTargetResolver({
    resolveEvolveBotRuntimeForOwner,
  } as unknown as EvolveRepository);
  return { resolver, resolveEvolveBotRuntimeForOwner };
}

describe("RepositoryRepairTargetResolver", () => {
  it("resolves a BaaS target from the shared ClawWeb runtime catalog without OCB", async () => {
    const { resolver, resolveEvolveBotRuntimeForOwner } = resolverWith({
      botType: "personal",
      botStatus: "active",
      bindingId: "binding-1",
      bindingStatus: "active",
      provider: "baas",
      deviceId: "device-1",
      env: "pre",
    });

    await expect(resolver.resolve({ environment: "pre", ownerId: "405935", botId: "bot-1" }))
      .resolves.toMatchObject({
        environment: "pre",
        ownerId: "405935",
        botId: "bot-1",
        provider: "baas",
        bindingId: "binding-1",
        deviceId: "device-1",
        source: "clawweb_runtime_catalog",
      });
    expect(resolveEvolveBotRuntimeForOwner).toHaveBeenCalledWith("405935", "bot-1", "pre");
  });

  it("keeps the raw ARCA instance suffix only for scoped proxy signing", async () => {
    const { resolver } = resolverWith({
      botType: "personal",
      botStatus: "active",
      bindingId: "binding-2",
      bindingStatus: "active",
      provider: "arca",
      deviceId: "legacy-device",
      arcaInstanceId: "ARCA-SANDBOX-123@9",
      env: "prod",
    });

    await expect(resolver.resolve({ environment: "prod", ownerId: "1", botId: "legacy" }))
      .resolves.toMatchObject({
        sandboxId: "ARCA-SANDBOX-123",
        arcaInstanceId: "ARCA-SANDBOX-123@9",
      });
  });

  it("fails closed for missing, non-personal, or environment-mismatched targets", async () => {
    await expect(resolverWith(null).resolver.resolve({ environment: "pre", ownerId: "1", botId: "missing" }))
      .rejects.toMatchObject({ status: 404, code: "repair_target_not_found" });

    await expect(resolverWith({
      botType: "service", bindingId: "1", provider: "baas", deviceId: "1", env: "pre",
    }).resolver.resolve({ environment: "pre", ownerId: "1", botId: "service" }))
      .rejects.toMatchObject({ status: 422, code: "unsupported_scope" });

    await expect(resolverWith({
      botType: "personal", bindingId: "1", provider: "baas", deviceId: "1", env: "prod",
    }).resolver.resolve({ environment: "pre", ownerId: "1", botId: "wrong-env" }))
      .rejects.toMatchObject({ status: 409, code: "target_environment_mismatch" });
  });
});
