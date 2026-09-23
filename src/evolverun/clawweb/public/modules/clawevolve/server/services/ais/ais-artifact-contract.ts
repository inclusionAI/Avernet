export type AisArtifactSpec = {
  objectKey: string;
  contentType?: string;
  requiredOnSuccess?: boolean;
  allowEmpty?: boolean;
};

export type AisArtifactContract = {
  aisBase: Record<string, unknown>;
  stepId: string;
  artifacts: Record<string, AisArtifactSpec & Required<Pick<AisArtifactSpec,
    "contentType" | "requiredOnSuccess" | "allowEmpty">>>;
  artifactAnyOfOnSuccess?: string[][];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function parseAisArtifactContract(value: unknown): AisArtifactContract | null {
  if (!isRecord(value) || !isRecord(value.aisBase) || typeof value.stepId !== "string"
    || !isRecord(value.artifacts)) return null;
  const artifacts: AisArtifactContract["artifacts"] = {};
  for (const [name, raw] of Object.entries(value.artifacts)) {
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/u.test(name) || !isRecord(raw)
      || typeof raw.objectKey !== "string" || !raw.objectKey
      || typeof raw.contentType !== "string" || !raw.contentType || raw.contentType.length > 200
      || /[\r\n\0]/u.test(raw.contentType)
      || typeof raw.requiredOnSuccess !== "boolean" || typeof raw.allowEmpty !== "boolean") return null;
    artifacts[name] = {
      objectKey: raw.objectKey,
      contentType: raw.contentType,
      requiredOnSuccess: raw.requiredOnSuccess,
      allowEmpty: raw.allowEmpty,
    };
  }
  const groups = value.artifactAnyOfOnSuccess;
  if (groups != null && (!Array.isArray(groups) || groups.some(group => !Array.isArray(group)
    || group.length < 2 || group.some(name => typeof name !== "string" || !artifacts[name])))) return null;
  return {
    aisBase: value.aisBase,
    stepId: value.stepId,
    artifacts,
    ...(groups == null ? {} : { artifactAnyOfOnSuccess: groups as string[][] }),
  };
}

export function validateAisArtifactRequest(
  contract: AisArtifactContract,
  name: unknown,
  body: Record<string, unknown>,
): { name: string; spec: AisArtifactContract["artifacts"][string]; size: number; sha256: string } {
  if (typeof name !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/u.test(name)) {
    throw new Error("Artifact 名称不合法");
  }
  const spec = contract.artifacts[name];
  if (!spec) throw new Error("Artifact 未在任务合同中登记");
  const size = Number(body.size);
  const sha256 = String(body.sha256 ?? "");
  if (!Number.isSafeInteger(size) || size < (spec.allowEmpty ? 0 : 1)
    || !/^[0-9a-f]{64}$/u.test(sha256)) throw new Error("Artifact size 或 sha256 不合法");
  if (body.contentType !== spec.contentType) throw new Error("Artifact Content-Type 不合法");
  return { name, spec, size, sha256 };
}

export function validateAisOutput(
  taskId: string,
  contract: AisArtifactContract,
  output: unknown,
  partial: boolean,
): asserts output is Record<string, unknown> {
  if (!isRecord(output) || output.taskId !== taskId || output.success !== !partial
    || !isRecord(output.artifacts)) throw new Error("AIS 结果身份或状态不匹配");
  const uploaded = output.artifacts;
  if (!partial) {
    for (const [name, spec] of Object.entries(contract.artifacts)) {
      if (spec.requiredOnSuccess && !(name in uploaded)) throw new Error(`AIS 结果缺少 ${name} 产物`);
    }
    for (const group of contract.artifactAnyOfOnSuccess ?? []) {
      if (!group.some(name => name in uploaded)) throw new Error(`AIS 结果缺少 ${group.join("/")} 产物`);
    }
  }
  for (const [name, raw] of Object.entries(uploaded)) {
    const spec = contract.artifacts[name];
    if (!spec || !isRecord(raw) || raw.objectKey !== spec.objectKey
      || !Number.isSafeInteger(raw.size) || Number(raw.size) < (spec.allowEmpty ? 0 : 1)
      || typeof raw.sha256 !== "string" || !/^[0-9a-f]{64}$/u.test(raw.sha256)
      || raw.contentType !== spec.contentType) throw new Error(`AIS ${name} 产物元数据无效`);
  }
}
