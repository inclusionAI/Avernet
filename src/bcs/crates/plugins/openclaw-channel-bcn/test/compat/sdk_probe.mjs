#!/usr/bin/env node

import { readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mkdir } from 'node:fs/promises';
import process from 'node:process';

const EXPECTED_EXPORTS = {
  './plugin-sdk/core': [ 'emptyPluginConfigSchema' ],
  './plugin-sdk/account-id': [ 'DEFAULT_ACCOUNT_ID' ],
  './plugin-sdk/channel-config-helpers': [
    'createScopedChannelConfigBase',
    'createScopedAccountConfigAccessors',
    'createScopedDmSecurityResolver',
  ],
  './plugin-sdk/allow-from': [ 'formatAllowFromLowercase' ],
  './plugin-sdk/runtime-store': [ 'createPluginRuntimeStore' ],
};

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith('--') || value === undefined) throw new Error(`invalid argument: ${key}`);
    result[key.slice(2)] = value;
  }
  if (!result['package-root']) throw new Error('--package-root is required');
  return result;
}

function importTarget(entry) {
  if (typeof entry === 'string') return entry;
  if (!entry || typeof entry !== 'object') return undefined;
  for (const key of [ 'import', 'node', 'default' ]) {
    const selected = importTarget(entry[key]);
    if (selected) return selected;
  }
  return undefined;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const packageRoot = args['package-root'];
  const packageJson = JSON.parse(await readFile(join(packageRoot, 'package.json'), 'utf8'));
  const checks = [];

  for (const [ subpath, expected ] of Object.entries(EXPECTED_EXPORTS)) {
    const target = importTarget(packageJson.exports?.[subpath]);
    if (!target) {
      checks.push({ subpath, ok: false, missing: expected, reason: 'package export is missing' });
      continue;
    }
    try {
      const module = await import(pathToFileURL(join(packageRoot, target)).href);
      const missing = expected.filter(name => !(name in module));
      checks.push({ subpath, target, ok: missing.length === 0, missing });
    } catch (error) {
      checks.push({
        subpath,
        target,
        ok: false,
        missing: expected,
        reason: error instanceof Error ? error.message : String(error),
      });
    }
  }

  const payload = {
    ok: checks.every(check => check.ok),
    package: packageJson.name,
    version: packageJson.version,
    engines: packageJson.engines ?? {},
    checks,
  };
  const rendered = `${JSON.stringify(payload, null, 2)}\n`;
  if (args.output) {
    await mkdir(dirname(args.output), { recursive: true });
    await writeFile(args.output, rendered, 'utf8');
  } else {
    process.stdout.write(rendered);
  }
  if (!payload.ok) process.exitCode = 1;
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
