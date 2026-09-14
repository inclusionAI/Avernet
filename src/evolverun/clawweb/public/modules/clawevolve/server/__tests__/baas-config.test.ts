import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { resolveBaasConfig } from "@avernet/clawweb-shared/server/db";

const temporaryDirectories: string[] = [];
const originalPreApiKey = process.env.CLAWEVOLVE_BAAS_PRE_API_KEY;
const originalProdApiKey = process.env.CLAWEVOLVE_BAAS_PROD_API_KEY;
const originalDevApiKey = process.env.CLAWEVOLVE_BAAS_DEV_API_KEY;

function configFile(): string {
  const directory = mkdtempSync(join(tmpdir(), "clawevolve-baas-config-"));
  temporaryDirectories.push(directory);
  const path = join(directory, "application.yaml");
  writeFileSync(path, `
baas:
  environments:
    dev:
      baseUrl: http://127.0.0.1:8890
    pre:
      baseUrl: https://baas-pre.example.com
    prod:
      baseUrl: https://baas-prod.example.com
  evolveScriptPaths:
    dev: /runner/dev.sh
    pre: /runner/pre.sh
    prod: /runner/prod.sh
`, "utf8");
  return path;
}

afterEach(() => {
  if (originalPreApiKey === undefined) delete process.env.CLAWEVOLVE_BAAS_PRE_API_KEY;
  else Reflect.set(process.env, "CLAWEVOLVE_BAAS_PRE_API_KEY", originalPreApiKey);
  if (originalProdApiKey === undefined) delete process.env.CLAWEVOLVE_BAAS_PROD_API_KEY;
  else Reflect.set(process.env, "CLAWEVOLVE_BAAS_PROD_API_KEY", originalProdApiKey);
  if (originalDevApiKey === undefined) delete process.env.CLAWEVOLVE_BAAS_DEV_API_KEY;
  else Reflect.set(process.env, "CLAWEVOLVE_BAAS_DEV_API_KEY", originalDevApiKey);
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { recursive: true, force: true });
  }
});

describe("BaaS runtime credentials", () => {
  it("uses process-local credentials loaded by the OCB MIST adapter", () => {
    process.env.CLAWEVOLVE_BAAS_PRE_API_KEY = "pre-key";
    process.env.CLAWEVOLVE_BAAS_PROD_API_KEY = "prod-key";
    process.env.CLAWEVOLVE_BAAS_DEV_API_KEY = "dev-key";

    expect(resolveBaasConfig(configFile()).environments).toEqual({
      dev: { apiKey: "dev-key", baseUrl: "http://127.0.0.1:8890" },
      pre: { apiKey: "pre-key", baseUrl: "https://baas-pre.example.com" },
      prod: { apiKey: "prod-key", baseUrl: "https://baas-prod.example.com" },
    });
  });
});
