import { createHash, timingSafeEqual } from "node:crypto";
import { isMap, isScalar, parseDocument } from "yaml";
import type { AppConfigRepository } from "../repositories/app-config-repository.js";

export type ExecutionRecordTokenAuth = "authenticated" | "unauthorized" | "unavailable";

/** Accept one plain YAML mapping, without aliases, extra fields or implicit numeric digests. */
function readExpectedDigest(configYaml: string): Buffer | null {
  // "silent" suppresses YAML's MULTIPLE_DOCS error too; parseDocument itself never logs source.
  const document = parseDocument(configYaml, {
    uniqueKeys: true, prettyErrors: false, strict: true, logLevel: "error",
    schema: "core", merge: false, resolveKnownTags: false,
  });
  if (document.errors.length || document.warnings.length || !isMap(document.contents)) return null;
  if (document.contents.items.length !== 1) return null;
  const { key, value } = document.contents.items[0];
  if (!isScalar(key) || key.value !== "execution_record_sha256" || !isScalar(value)) return null;
  const config: unknown = document.toJS({ maxAliasCount: 0 });
  if (!config || typeof config !== "object" || Object.getPrototypeOf(config) !== Object.prototype) return null;
  const digest: unknown = (config as Record<string, unknown>).execution_record_sha256;
  if (typeof digest !== "string" || digest.length !== 64 || !/^[0-9a-fA-F]{64}$/.test(digest)) return null;
  return Buffer.from(digest, "hex");
}

/** Transport-independent verification. Config is deliberately never cached; failures disclose no details. */
export async function verifyExecutionRecordToken(
  configRepo: Pick<AppConfigRepository, "findByKey"> | null | undefined,
  authorization: string | string[] | undefined,
  rawHeaders?: readonly string[],
): Promise<ExecutionRecordTokenAuth> {
  let expected: Buffer | null;
  try {
    if (!configRepo) return "unavailable";
    const row = await configRepo.findByKey("api_token");
    if (!row || row.config_key !== "api_token" || row.enabled !== 1 || typeof row.config_yaml !== "string") return "unavailable";
    expected = readExpectedDigest(row.config_yaml);
    if (!expected || expected.length !== 32) return "unavailable";
  } catch {
    // Do not inspect errors: drivers and YAML parsers may embed credentials or source text.
    return "unavailable";
  }

  // Node may discard duplicate Authorization values from normalized headers; inspect originals too.
  let authorizationCount = 0;
  for (let index = 0; rawHeaders && index < rawHeaders.length; index += 2) {
    if (rawHeaders[index].toLowerCase() === "authorization") authorizationCount++;
  }
  if (authorizationCount > 1 || typeof authorization !== "string") return "unauthorized";
  const separator = authorization.indexOf(" ");
  const scheme = authorization.slice(0, separator);
  const bearerValue = authorization.slice(separator + 1);
  if (scheme.toLowerCase() !== "bearer" || bearerValue.length !== 64 || !/^[0-9a-f]{64}$/.test(bearerValue)) return "unauthorized";
  const actual = createHash("sha256").update(bearerValue, "utf8").digest();
  return actual.length === expected.length && timingSafeEqual(actual, expected) ? "authenticated" : "unauthorized";
}
