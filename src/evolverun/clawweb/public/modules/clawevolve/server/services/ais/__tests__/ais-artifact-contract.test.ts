import { describe, expect, it } from "vitest";
import {
  parseAisArtifactContract,
  validateAisArtifactRequest,
  validateAisOutput,
} from "../ais-artifact-contract.js";

const contract = parseAisArtifactContract({
  aisBase: { packageId: "clawevolve-ais-diagnose" },
  stepId: "SA-1-AIS",
  artifacts: {
    raw: {
      objectKey: "evolution/SA-1/raw.jsonl",
      contentType: "application/x-ndjson",
      requiredOnSuccess: false,
      allowEmpty: false,
    },
    trajectory: {
      objectKey: "evolution/SA-1/trajectory.jsonl",
      contentType: "application/x-ndjson",
      requiredOnSuccess: false,
      allowEmpty: true,
    },
    result: {
      objectKey: "evolution/SA-1/result.json",
      contentType: "application/json",
      requiredOnSuccess: true,
      allowEmpty: false,
    },
  },
  artifactAnyOfOnSuccess: [["raw", "trajectory"]],
})!;

function artifact(name: "raw" | "trajectory" | "result", size = 10) {
  const spec = contract.artifacts[name];
  return { objectKey: spec.objectKey, size, sha256: "a".repeat(64), contentType: spec.contentType };
}

describe("AIS artifact contract", () => {
  it("binds upload requests to a frozen name, path, type, size and digest", () => {
    expect(validateAisArtifactRequest(contract, "result", {
      size: 10, sha256: "a".repeat(64), contentType: "application/json",
    })).toMatchObject({ name: "result", size: 10, spec: contract.artifacts.result });
    expect(() => validateAisArtifactRequest(contract, "unknown", {
      size: 10, sha256: "a".repeat(64), contentType: "application/json",
    })).toThrow("未在任务合同中登记");
  });

  it("requires the success artifacts while preserving verified failure evidence", () => {
    expect(() => validateAisOutput("SA-1", contract, {
      taskId: "SA-1", success: true, artifacts: { raw: artifact("raw"), result: artifact("result") },
    }, false)).not.toThrow();
    expect(() => validateAisOutput("SA-1", contract, {
      taskId: "SA-1", success: true, artifacts: { result: artifact("result") },
    }, false)).toThrow("raw/trajectory");
    expect(() => validateAisOutput("SA-1", contract, {
      taskId: "SA-1", success: false, artifacts: { trajectory: artifact("trajectory", 0) },
    }, true)).not.toThrow();
  });
});
