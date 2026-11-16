import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resolveAutoHealConfig, resolveBaasConfig, type ResolvedBaasConfig } from "../db.js";

import { selectAutoHealBaasTransport, type AutoHealBaasEnvironment } from "../auto-heal-baas.js";

let configDirectory: string;

function writeConfig(config: Record<string, unknown>): string {
  const path = join(configDirectory, "application.yaml");
  writeFileSync(path, JSON.stringify(config));
  return path;
}

beforeEach(() => {
  configDirectory = mkdtempSync(join(tmpdir(), "clawweb-auto-heal-baas-"));
  for (const name of [
    "AUTO_HEAL_BAAS_ENVIRONMENT", "CLAWEVOLVE_BAAS_DEV_API_KEY",
    "CLAWEVOLVE_BAAS_PRE_API_KEY", "CLAWEVOLVE_BAAS_PROD_API_KEY",
    "SERVER_ENV", "REAL_SERVER_ENV", "ALIPAY_APP_ENV",
  ]) vi.stubEnv(name, undefined);
});

afterEach(() => {
  vi.unstubAllEnvs();
  rmSync(configDirectory, { recursive: true, force: true });
});

describe("resolveAutoHealConfig BaaS targeting", () => {
  it("defaults an absent auto-heal target to legacy prod", () => {
    expect(resolveAutoHealConfig(writeConfig({}))).toMatchObject({ baasEnvironment: "prod" });
    expect(resolveAutoHealConfig(writeConfig({ autoHeal: {} })))
      .toMatchObject({ baasEnvironment: "prod" });
  });

  it.each(["dev", "pre", "prod"])("accepts the exact YAML target %s", (baasEnvironment) => {
    expect(resolveAutoHealConfig(writeConfig({ autoHeal: { baasEnvironment } })))
      .toMatchObject({ baasEnvironment });
  });

  it("gives the environment override precedence over YAML, including invalid YAML", () => {
    vi.stubEnv("AUTO_HEAL_BAAS_ENVIRONMENT", "dev");
    const path = writeConfig({ autoHeal: { baasEnvironment: "pre" } });
    expect(resolveAutoHealConfig(path)).toMatchObject({ baasEnvironment: "dev" });
    writeConfig({ autoHeal: { baasEnvironment: null } });
    expect(resolveAutoHealConfig(path)).toMatchObject({ baasEnvironment: "dev" });
  });

  it.each(["", " ", "\t", "gray", "prepub", "PRE", " pre "])(
    "rejects the explicit environment override %j instead of using YAML",
    (value) => {
      vi.stubEnv("AUTO_HEAL_BAAS_ENVIRONMENT", value);
      expect(() => resolveAutoHealConfig(writeConfig({ autoHeal: { baasEnvironment: "pre" } })))
        .toThrow(new Error("Invalid auto-heal BaaS environment"));
    },
  );

  it.each(["", " ", "gray", "prepub", "PROD", " prod ", null, false, 1, [], {}])(
    "rejects the explicit YAML target %j without echoing input",
    (baasEnvironment) => {
      expect(() => resolveAutoHealConfig(writeConfig({ autoHeal: { baasEnvironment } })))
        .toThrow(new Error("Invalid auto-heal BaaS environment"));
    },
  );

  it.each(["dev", "pre", "prepub", "gray", "prod"])(
    "does not derive an auto-heal target from the host environment %s",
    (hostEnvironment) => {
      for (const name of ["SERVER_ENV", "REAL_SERVER_ENV", "ALIPAY_APP_ENV"])
        vi.stubEnv(name, hostEnvironment);
      expect(resolveAutoHealConfig(writeConfig({}))).toMatchObject({ baasEnvironment: "prod" });
      expect(resolveAutoHealConfig(writeConfig({ autoHeal: { baasEnvironment: "dev" } })))
        .toMatchObject({ baasEnvironment: "dev" });
    },
  );
});

function baasFixture(): ResolvedBaasConfig {
  return {
    apiKey: 'provider-test', baseUrl: 'https://prod.example.invalid/api', iamtoken: 'baas-test',
    environments: {
      dev: { apiKey: 'baas-test', baseUrl: 'http://localhost/dev' },
      pre: { apiKey: 'baas-test', baseUrl: 'https://pre.example.invalid/api' },
      prod: { apiKey: 'provider-test', baseUrl: 'https://prod.example.invalid/api' },
    },
    evolveScriptPaths: { dev: '/runner/dev.sh', pre: '/runner/pre.sh', prod: '/runner/prod.sh' },
    commandTenant: 'test', commandTimeoutSeconds: 60,
  };
}
const select = (config: ResolvedBaasConfig, baasEnvironment: AutoHealBaasEnvironment = 'pre') =>
  selectAutoHealBaasTransport({ baasEnvironment }, config);

describe('AutoHeal complete BaaS target pair selection', () => {
  it('uses the configured pre pair instead of top-level prod, sharing only IAM', () => {
    const baas = baasFixture();
    const path = writeConfig({ autoHeal: { baasEnvironment: 'pre' }, baas });
    const resolved = resolveBaasConfig(path);
    expect(resolved.baseUrl).toBe(baas.baseUrl);
    expect(selectAutoHealBaasTransport(resolveAutoHealConfig(path), resolved)).toEqual({
      environment: 'pre', ...baas.environments.pre, iamtoken: baas.iamtoken,
    });
  });
  it('selects the complete dev pair without production credentials', () => {
    const baas = baasFixture();
    expect(select(baas, 'dev')).toEqual({ environment: 'dev', ...baas.environments.dev, iamtoken: baas.iamtoken });
  });
  it('preserves default-prod compatibility via the existing resolved prod fallback', () => {
    const baas = baasFixture();
    const path = writeConfig({ baas: { ...baas, environments: {} } });
    expect(selectAutoHealBaasTransport(resolveAutoHealConfig(path), resolveBaasConfig(path))).toEqual({
      environment: 'prod', apiKey: baas.apiKey, baseUrl: baas.baseUrl, iamtoken: baas.iamtoken,
    });
  });
  it('honors the existing environment credential override without using top-level prod', () => {
    const baas = baasFixture(), path = writeConfig({ autoHeal: { baasEnvironment: 'pre' }, baas });
    vi.stubEnv('CLAWEVOLVE_BAAS_PRE_API_KEY', 'provider-test');
    expect(selectAutoHealBaasTransport(resolveAutoHealConfig(path), resolveBaasConfig(path))).toEqual({
      environment: 'pre', apiKey: 'provider-test', baseUrl: baas.environments.pre.baseUrl, iamtoken: baas.iamtoken,
    });
  });
  it.each([
    {}, { apiKey: 'baas-test' }, { baseUrl: 'https://pre.example.invalid/api' },
    { apiKey: '', baseUrl: 'https://pre.example.invalid/api' },
    { apiKey: 'baas-test', baseUrl: '' },
  ])('does not fill an incomplete pre pair from top-level or prod: %j', pair => {
    const baas = baasFixture(), path = writeConfig({ autoHeal: { baasEnvironment: 'pre' },
      baas: { ...baas, environments: { ...baas.environments, pre: pair } } });
    expect(() => selectAutoHealBaasTransport(resolveAutoHealConfig(path), resolveBaasConfig(path)))
      .toThrow(/^BAAS_NOT_CONFIGURED$/);
  });
  it.each([
    { apiKey: '' }, { apiKey: '   ' }, { apiKey: null }, { apiKey: 1 }, { apiKey: [] }, { apiKey: {} },
    { apiKey: 'baas-test\r\n' }, { baseUrl: '' }, { baseUrl: '   ' }, { baseUrl: null },
    { baseUrl: 1 }, { baseUrl: [] }, { baseUrl: {} }, { baseUrl: 'not-a-url' },
    { baseUrl: 'ftp://pre.example.invalid' }, { baseUrl: 'https://user:password@pre.example.invalid' },
    { baseUrl: 'https://pre.example.invalid?secret=value' }, { baseUrl: 'https://pre.example.invalid#secret' },
    { baseUrl: 'https://pre.\nexample.invalid' },
  ])('rejects a malformed selected pair without echoing its values: %j', change => {
    const baas = baasFixture();
    baas.environments.pre = { ...baas.environments.pre, ...change } as unknown as ResolvedBaasConfig['environments']['pre'];
    expect(() => select(baas)).toThrow(/^BAAS_NOT_CONFIGURED$/);
  });
  it.each([null, undefined, {}, []])('rejects a missing/malformed target entry: %j', pair => {
    const baas = baasFixture();
    baas.environments.pre = pair as unknown as ResolvedBaasConfig['environments']['pre'];
    expect(() => select(baas)).toThrow(/^BAAS_NOT_CONFIGURED$/);
  });
  it('rejects a missing environment map, never using the complete top-level pair', () => {
    const baas = baasFixture(); Reflect.deleteProperty(baas, 'environments');
    expect(() => select(baas)).toThrow(/^BAAS_NOT_CONFIGURED$/);
  });
  it.each(['', 'prepub', null, 1])('rejects an invalid selector input even if callers bypass config types: %j', value => {
    expect(() => select(baasFixture(), value as AutoHealBaasEnvironment)).toThrow(/^BAAS_NOT_CONFIGURED$/);
  });
  it('canonicalizes the complete endpoint path with the same shared selection for all consumers', () => {
    const baas = baasFixture(); baas.environments.pre.baseUrl = 'HTTP://LOCALHOST:80/parent/../provider/tenant-a/';
    expect(select(baas).baseUrl).toBe('http://localhost/provider/tenant-a');
  });
  it('returns copied scalars so later mutable configuration cannot change a frozen selection', () => {
    const baas = baasFixture(), selected = select(baas);
    baas.environments.pre.apiKey = 'provider-test'; baas.environments.pre.baseUrl = 'https://other.example.invalid';
    baas.iamtoken = 'provider-test';
    expect(selected).toEqual({ environment: 'pre', apiKey: 'baas-test', baseUrl: 'https://pre.example.invalid/api', iamtoken: 'baas-test' });
  });
  it('allows an empty shared IAM token but rejects malformed token configuration without revealing it', () => {
    const baas = baasFixture(); baas.iamtoken = '';
    expect(select(baas).iamtoken).toBe('');
    baas.iamtoken = { private: 'baas-test' } as unknown as string;
    expect(() => select(baas)).toThrow(/^BAAS_NOT_CONFIGURED$/);
  });
});
