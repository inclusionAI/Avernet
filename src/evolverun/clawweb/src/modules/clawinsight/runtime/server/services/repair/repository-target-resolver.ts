import type { EvolveRepository } from "../../repositories/evolve-repository.js";
import type { RepairTarget, RepairTargetEnvironment } from "./contracts.js";
import { RepairError, repairValidation } from "./errors.js";
import { normalizeArcaSandboxId } from "./arca-command-transport.js";

function required(value: string | number | null | undefined, field: string): string {
  const text = value == null ? "" : String(value).trim();
  if (!text) repairValidation("invalid_repair_target", `Bot 运行目标缺少 ${field}`);
  return text;
}

export interface RepairTargetResolver {
  resolve(input: {
    environment: RepairTargetEnvironment;
    ownerId: string;
    botId: string;
  }): Promise<RepairTarget>;
}

/** Resolves the frozen Repair target from ClawWeb's shared runtime catalog without calling OCB APIs. */
export class RepositoryRepairTargetResolver implements RepairTargetResolver {
  constructor(private readonly repo: EvolveRepository) {}

  async resolve(input: {
    environment: RepairTargetEnvironment;
    ownerId: string;
    botId: string;
  }): Promise<RepairTarget> {
    const runtime = await this.repo.resolveEvolveBotRuntimeForOwner(
      input.ownerId,
      input.botId,
      input.environment,
    );
    if (!runtime) {
      throw new RepairError(404, "repair_target_not_found", "所选 Bot 不存在或运行目标不可用");
    }
    if (runtime.botType?.toLowerCase() !== "personal") {
      throw new RepairError(422, "unsupported_scope", "Repair 当前仅支持个人 Bot");
    }
    if (runtime.env?.toLowerCase() !== input.environment) {
      throw new RepairError(409, "target_environment_mismatch", "Bot 当前运行环境与 Repair 任务不一致");
    }
    const provider = required(runtime.provider, "device_provider").toLowerCase();
    const bindingId = required(runtime.bindingId, "binding_id");
    const deviceId = required(runtime.deviceId, "device_id");
    let sandboxId: string | undefined;
    let arcaInstanceId: string | undefined;
    if (provider === "arca") {
      arcaInstanceId = required(runtime.arcaInstanceId, "ARCA sandbox_id");
      sandboxId = normalizeArcaSandboxId(arcaInstanceId);
    }
    return {
      environment: input.environment,
      ownerId: input.ownerId,
      botId: input.botId,
      botType: "personal",
      botStatus: runtime.botStatus,
      bindingId,
      bindingStatus: runtime.bindingStatus,
      provider,
      deviceId,
      ...(sandboxId ? { sandboxId, arcaInstanceId } : {}),
      observedAt: new Date().toISOString(),
      source: "clawweb_runtime_catalog",
    };
  }
}
