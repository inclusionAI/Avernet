import type { BotSkillGateway } from "../../contracts/bot-skill-gateway.js";
import type { RequestIdentity } from "../../contracts/request-identity.js";
import { createHash } from "node:crypto";
import type { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { skillPackagesEquivalent } from "./skill-package-view.js";

type VersionApplication =
  | { kind: "manual"; data: Parameters<SkillAssetRepository["createManualVersion"]>[0] }
  | { kind: "task"; data: Parameters<SkillAssetRepository["createAcceptedVersion"]>[0] };

/** One application boundary for HTTP editing and task acceptance. Frozen intent
 * survives a process/database failure after the external provider has changed. */
export async function applySkillVersion(input: {
  repo: SkillAssetRepository;
  host: BotSkillGateway;
  operationId: string;
  version: VersionApplication;
  readPackage: (ref: string) => Promise<Buffer>;
  replace: Omit<Parameters<BotSkillGateway["replaceLocalSkill"]>[0], "packageBytes">;
  baselineBytes?: Buffer;
}) {
  const data = input.version.data;
  const reservation = await input.repo.reserveApplication(data.assetId, {
    operationId: input.operationId, packageRef: data.packageRef, packageSha256: data.packageSha256,
  }, input.version.kind === "manual" ? input.version.data.baseVersionId : undefined,
  { versionId: data.versionId, sourceTaskId: input.version.kind === "task" ? input.version.data.sourceTaskId : undefined });
  if (reservation.completedVersion) return { version: reservation.completedVersion, sha256: reservation.completedVersion.package_sha256 };
  const intent = reservation.intent!;
  let applied;
  let packageReady = false;
  try {
    const packageBytes = await input.readPackage(intent.packageRef);
    if (`sha256:${createHash("sha256").update(packageBytes).digest("hex")}` !== intent.packageSha256) {
      throw new Error("冻结的 Skill 版本摘要不一致");
    }
    packageReady = true;
    applied = await applySkillPackage({ host: input.host, ...input.replace,
      packageBytes, baselineBytes: input.baselineBytes });
  } catch (error) {
    if (!reservation.resumed && (!packageReady || (error as { writeNotStarted?: boolean })?.writeNotStarted === true)) {
      await input.repo.releaseUnstartedApplication(data.assetId, input.operationId, intent.reservationId);
    }
    if (packageReady && error instanceof Error) Object.assign(error, { skillApplicationPhase: "host" });
    throw error;
  }
  const frozen = { packageRef: intent.packageRef, packageSha256: intent.packageSha256, operationId: input.operationId };
  const version = input.version.kind === "manual"
    ? await input.repo.createManualVersion({ ...input.version.data, ...frozen })
    : await input.repo.createAcceptedVersion({ ...input.version.data, ...frozen });
  return { version, sha256: applied.sha256 };
}

/** Both manual and task application use this recovery rule after durable intent.
 * No automatic retry of a mutation: a lost response is resolved by reading back.
 */
export async function applySkillPackage(input: {
  host: BotSkillGateway;
  botId: string;
  skillId: string;
  ownerUserId: string;
  identity: RequestIdentity;
  expectedSha256: string;
  packageBytes: Buffer;
  baselineBytes?: Buffer;
}): Promise<{ sha256: string }> {
  const { host, baselineBytes, ...request } = input;
  if (baselineBytes) {
    try {
      const live = await host.exportLocalSkill(request);
      if (await skillPackagesEquivalent(live.packageBytes, request.packageBytes)) return { sha256: live.sha256 };
      if (!await skillPackagesEquivalent(live.packageBytes, baselineBytes)) {
        throw Object.assign(new Error("Skill 已被其他操作修改，请刷新后重试"), {
          status: 409, code: "SKILL_VERSION_CONFLICT", writeNotStarted: true,
        });
      }
      request.expectedSha256 = live.sha256;
    } catch (error) {
      if (error instanceof Error) Object.assign(error, { writeNotStarted: true });
      throw error;
    }
  }
  try {
    return await host.replaceLocalSkill(request);
  } catch (error) {
    try {
      const live = await host.exportLocalSkill(request);
      if (await skillPackagesEquivalent(live.packageBytes, request.packageBytes)) return { sha256: live.sha256 };
    } catch { /* Preserve the original failure unless equality is proved. */ }
    throw error;
  }
}
