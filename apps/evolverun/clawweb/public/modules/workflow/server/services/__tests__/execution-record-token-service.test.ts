// @vitest-environment node
import { createHash } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import type { AppConfigRow } from "../../repositories/app-config-repository.js";
import { verifyExecutionRecordToken } from "../execution-record-token-service.js";

const token = "ef".repeat(32);
const digest = createHash("sha256").update(token, "utf8").digest("hex");
function repo() {
  return { findByKey: vi.fn(async () => ({ config_key: "api_token", enabled: 1,
    config_yaml: `execution_record_sha256: "${digest}"` } as AppConfigRow)) };
}

describe("execution record token verification boundary", () => {
  it.each([
    ` Bearer ${token}`, `Bearer ${token} `, `Bearer ${token}\n`, `Bearer ${token}\r\n`,
    `Bearer ${token}\t`, `Bearer \t${token}`, `Bearer ${token.toUpperCase()}`, `Bearer ${digest}`,
  ])("does not normalize, trim or accept a stored digest as raw credentials (%j)", async header => {
    expect(await verifyExecutionRecordToken(repo(), header)).toBe("unauthorized");
  });

  it("rejects array-valued Authorization even if one entry is valid", async () => {
    expect(await verifyExecutionRecordToken(repo(), [`Bearer ${token}`])).toBe("unauthorized");
  });

  it("verifies the SHA256 of token UTF8 text rather than decoding the token hex first", async () => {
    const config = repo();
    expect(await verifyExecutionRecordToken(config, `Bearer ${token}`)).toBe("authenticated");
    const decodedDigest = createHash("sha256").update(Buffer.from(token, "hex")).digest("hex");
    config.findByKey.mockResolvedValue({ config_key: "api_token", enabled: 1,
      config_yaml: `execution_record_sha256: "${decodedDigest}"` } as AppConfigRow);
    expect(await verifyExecutionRecordToken(config, `Bearer ${token}`)).toBe("unauthorized");
  });

  it("returns safe configuration failures without requiring request credentials", async () => {
    expect(await verifyExecutionRecordToken(undefined, undefined)).toBe("unavailable");
    const config = repo();
    config.findByKey.mockRejectedValue(new Error(`private-yaml: ${digest}`));
    expect(await verifyExecutionRecordToken(config, undefined)).toBe("unavailable");
  });
});
