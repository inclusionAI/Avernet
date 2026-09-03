#!/usr/bin/env node

import {
  chmodSync,
  closeSync,
  existsSync,
  fsyncSync,
  lstatSync,
  openSync,
  readFileSync,
  realpathSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import * as yaml from 'js-yaml';
import { canonicalizeEndpoint } from './endpoint.js';

const PLUGIN_ID = 'deepseek-harness-channel-bcn';
const PROFILE_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const JS_EXPRESSION_KEY = '__jsExpr';

interface JsExpression {
  [JS_EXPRESSION_KEY]: string;
}

const jsExpressionType = new yaml.Type('tag:yaml.org,2002:js', {
  kind: 'scalar',
  resolve: data => typeof data === 'string',
  construct: data => ({ [JS_EXPRESSION_KEY]: data ?? '' }),
  predicate: (value): value is JsExpression => isRecord(value)
    && typeof value[JS_EXPRESSION_KEY] === 'string',
  represent: value => (value as JsExpression)[JS_EXPRESSION_KEY],
});
const patchSchema = yaml.JSON_SCHEMA.extend(jsExpressionType);

export interface ConfigureProfileOptions {
  profile: string;
  endpoint: string;
  botName: string;
  dshHome?: string;
  dshBin?: string;
}

export interface ConfigureProfileDependencies {
  validate?: (profile: string, dshHome: string, dshBin: string) => void;
}

export interface ConfigureProfileResult {
  profile: string;
  patchPath: string;
  endpoint: string;
}

export function configureProfile(
  options: ConfigureProfileOptions,
  dependencies: ConfigureProfileDependencies = {},
): ConfigureProfileResult {
  const profile = options.profile.trim();
  if (!PROFILE_PATTERN.test(profile)) {
    throw new Error('profile must match [a-z0-9][a-z0-9-]{0,63}');
  }
  const endpoint = canonicalizeEndpoint(options.endpoint.trim());
  const botName = options.botName.trim();
  if (Array.from(botName).length < 2 || Array.from(botName).length > 64) {
    throw new Error('botName must contain 2-64 characters');
  }

  const dshHome = resolve(options.dshHome ?? process.env.DSH_HOME ?? join(homedir(), '.dsh'));
  const profileDir = join(dshHome, 'profiles', profile);
  assertDirectoryWithoutSymlink(profileDir, 'DSH profile');
  const patchPath = join(profileDir, 'cordis.patch.yml');
  const original = readPatchFile(patchPath);
  const patches = parsePatchList(original.content, patchPath);
  const matchingIndexes = patches.flatMap((entry, index) => entry.id === PLUGIN_ID ? [index] : []);
  if (matchingIndexes.length > 1) {
    throw new Error(`profile contains multiple ${PLUGIN_ID} patches`);
  }

  const existingIndex = matchingIndexes[0];
  const existing = existingIndex === undefined ? undefined : patches[existingIndex];
  const existingConfig = isRecord(existing?.config) ? existing.config : {};
  const defaultConfig = {
    enabled: true,
    endpoint,
    botName,
    summary: 'General-purpose DeepSeek Harness agent',
    domains: ['general'],
    skills: ['chat'],
    scopes: ['chat'],
    onboardingTokenRef: 'BCN_ONBOARDING_TOKEN',
    botSessionRef: 'BCN_BOT_SESSION',
    connectionTimeoutMs: 10_000,
    heartbeatIntervalMs: 30_000,
    reconnectInitialMs: 1_000,
    reconnectMaxMs: 30_000,
  };
  const configured = {
    ...(existing ?? {}),
    id: PLUGIN_ID,
    config: {
      ...defaultConfig,
      ...existingConfig,
      enabled: true,
      endpoint,
      botName,
    },
  };
  if (existingIndex === undefined) patches.push(configured);
  else patches[existingIndex] = configured;

  const rendered = yaml.dump(patches, {
    schema: patchSchema,
    noRefs: true,
    lineWidth: 100,
    noCompatMode: true,
  });
  atomicWrite(patchPath, rendered, original.mode);
  try {
    const dshBin = options.dshBin ?? 'dsh';
    (dependencies.validate ?? validateWithDsh)(profile, dshHome, dshBin);
  } catch (error) {
    try {
      if (original.existed) atomicWrite(patchPath, original.content, original.mode);
      else unlinkSync(patchPath);
    } catch (rollbackError) {
      throw new Error('DSH config validation failed and rollback also failed', {
        cause: new AggregateError([error, rollbackError]),
      });
    }
    throw new Error('DSH config validation failed; restored the previous patch file', { cause: error });
  }
  return { profile, patchPath, endpoint };
}

function readPatchFile(path: string): { existed: boolean; content: string; mode: number } {
  if (!existsSync(path)) return { existed: false, content: '[]\n', mode: 0o600 };
  const stat = lstatSync(path);
  if (stat.isSymbolicLink() || !stat.isFile()) {
    throw new Error('DSH profile patch must be a regular file, not a symlink');
  }
  return { existed: true, content: readFileSync(path, 'utf8'), mode: stat.mode & 0o777 };
}

function parsePatchList(content: string, path: string): Record<string, unknown>[] {
  let parsed: unknown;
  try {
    parsed = yaml.load(content, { schema: patchSchema });
  } catch (error) {
    throw new Error(`failed to parse DSH profile patch ${path}`, { cause: error });
  }
  if (!Array.isArray(parsed) || !parsed.every(isRecord)) {
    throw new Error('DSH profile patch must be a top-level array of mappings');
  }
  return parsed;
}

function assertDirectoryWithoutSymlink(path: string, label: string): void {
  if (!existsSync(path)) throw new Error(`${label} does not exist: ${path}`);
  const stat = lstatSync(path);
  if (stat.isSymbolicLink() || !stat.isDirectory()) {
    throw new Error(`${label} must be a real directory, not a symlink`);
  }
}

function atomicWrite(path: string, content: string, mode: number): void {
  const parent = dirname(path);
  assertDirectoryWithoutSymlink(parent, 'DSH profile');
  const temporary = join(parent, `.cordis.patch.yml.${process.pid}.${randomUUID()}.tmp`);
  let descriptor: number | undefined;
  try {
    descriptor = openSync(temporary, 'wx', mode);
    writeFileSync(descriptor, content, 'utf8');
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = undefined;
    chmodSync(temporary, mode);
    renameSync(temporary, path);
  } catch (error) {
    if (descriptor !== undefined) closeSync(descriptor);
    try {
      unlinkSync(temporary);
    } catch {
      // The temporary file may not have been created or may already have been renamed.
    }
    throw error;
  }
}

function validateWithDsh(profile: string, dshHome: string, dshBin: string): void {
  // COSEC: spawnSync receives a fixed executable plus an argument vector; no shell parses profile input.
  const result = spawnSync(dshBin, ['--profile', profile, '--dump-config'], {
    shell: false,
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
    env: { ...process.env, DSH_HOME: dshHome },
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const diagnostic = (result.stderr || result.stdout || `exit status ${String(result.status)}`).trim();
    throw new Error(diagnostic);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

interface CliOptions {
  profile: string;
  endpoint: string;
  botName: string;
}

function parseArgs(args: string[]): CliOptions {
  const values = new Map<string, string>();
  for (let index = 0; index < args.length; index += 1) {
    const option = args[index];
    if (option !== '--profile' && option !== '--endpoint' && option !== '--bot-name') {
      throw new Error(`unknown option: ${option ?? ''}`);
    }
    const value = args[index + 1];
    if (!value || value.startsWith('--')) throw new Error(`${option} requires a value`);
    values.set(option, value);
    index += 1;
  }
  const profile = values.get('--profile');
  const endpoint = values.get('--endpoint');
  if (!profile || !endpoint) {
    throw new Error('usage: dsh-bcn-configure --profile <name> --endpoint <url> [--bot-name <name>]');
  }
  return { profile, endpoint, botName: values.get('--bot-name') ?? 'DeepSeek Harness Bot' };
}

const invokedPath = process.argv[1] ? realpathSync(resolve(process.argv[1])) : undefined;
if (invokedPath === fileURLToPath(import.meta.url)) {
  try {
    const result = configureProfile(parseArgs(process.argv.slice(2)));
    process.stdout.write(`Configured BCN channel for DSH profile ${result.profile}.\n`);
  } catch (error) {
    process.stderr.write(`dsh-bcn-configure: ${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
