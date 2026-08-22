import type { MockConfig, MockSource } from "../types.js";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parse as parseYaml } from "yaml";

export type ResolvedMock = {
  config: MockConfig | undefined;
  source: MockSource;
};

export class MockRegistry {
  private entries = new Map<string, { config: MockConfig; source: MockSource }>();

  register(nodeId: string, config: MockConfig, source: MockSource): void {
    this.entries.set(nodeId, { config, source });
  }

  resolve(nodeId: string): ResolvedMock {
    const entry = this.entries.get(nodeId);
    if (entry) return { config: entry.config, source: entry.source };
    return { config: undefined, source: "default" };
  }

  loadExternalFile(filePath: string): void {
    const absPath = resolve(filePath);
    let content: string;
    try {
      content = readFileSync(absPath, "utf-8");
    } catch (err) {
      throw new Error(`Mock file not found: ${absPath}`);
    }

    let parsed: unknown;
    try {
      parsed = parseYaml(content);
    } catch (err) {
      throw new Error(`Invalid YAML in mock file ${absPath}: ${(err as Error).message}`);
    }

    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error(`Mock file must be a YAML object mapping node IDs to mock configs: ${absPath}`);
    }

    const map = parsed as Record<string, unknown>;
    for (const [nodeId, rawMock] of Object.entries(map)) {
      if (rawMock == null || typeof rawMock !== "object" || Array.isArray(rawMock)) {
        throw new Error(`Invalid mock config for node "${nodeId}" in ${absPath}: must be an object`);
      }
      const mock = validateMockConfig(rawMock as Record<string, unknown>, nodeId, absPath);
      this.register(nodeId, mock, "external");
    }
  }

  buildFromWorkflow(nodes: { id: string; mock?: MockConfig }[]): void {
    for (const node of nodes) {
      if (node.mock) {
        this.register(node.id, node.mock, "inline");
      }
    }
  }

  applyTestOverrides(overrides: Record<string, MockConfig>): void {
    for (const [nodeId, config] of Object.entries(overrides)) {
      this.register(nodeId, config, "override");
    }
  }
}

function validateMockConfig(
  raw: Record<string, unknown>,
  nodeId: string,
  filePath: string,
): MockConfig {
  const mock: MockConfig = {};

  if (raw.output !== undefined) {
    if (typeof raw.output !== "object" || raw.output === null || Array.isArray(raw.output)) {
      throw new Error(
        `Mock for node "${nodeId}" in ${filePath}: output must be an object, got ${Array.isArray(raw.output) ? "array" : typeof raw.output}`,
      );
    }
    mock.output = raw.output as Record<string, unknown>;
  }

  if (raw.error !== undefined) {
    if (typeof raw.error !== "string") {
      throw new Error(`Mock for node "${nodeId}" in ${filePath}: error must be a string`);
    }
    mock.error = raw.error;
  }

  if (raw.timeout !== undefined) {
    if (typeof raw.timeout !== "boolean") {
      throw new Error(`Mock for node "${nodeId}" in ${filePath}: timeout must be a boolean`);
    }
    mock.timeout = raw.timeout;
  }

  if (raw.delay !== undefined) {
    if (typeof raw.delay !== "number" || raw.delay < 0 || !Number.isFinite(raw.delay)) {
      throw new Error(`Mock for node "${nodeId}" in ${filePath}: delay must be a non-negative number`);
    }
    mock.delay = raw.delay;
  }

  if (raw.autoConfirm !== undefined) {
    if (typeof raw.autoConfirm !== "boolean") {
      throw new Error(`Mock for node "${nodeId}" in ${filePath}: autoConfirm must be a boolean`);
    }
    mock.autoConfirm = raw.autoConfirm;
  }

  if (raw.maxIterations !== undefined) {
    if (typeof raw.maxIterations !== "number" || !Number.isInteger(raw.maxIterations) || raw.maxIterations < 1) {
      throw new Error(`Mock for node "${nodeId}" in ${filePath}: maxIterations must be a positive integer`);
    }
    mock.maxIterations = raw.maxIterations;
  }

  return mock;
}