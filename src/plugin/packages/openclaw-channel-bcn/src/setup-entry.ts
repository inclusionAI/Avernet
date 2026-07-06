/**
 * Setup entry for BCS channel plugin.
 *
 * When the plugin is first installed, `channels.bcs` doesn't exist in openclaw.json.
 * The framework detects this and loads setup-entry instead of the main index.ts.
 * This module:
 *   1. Exports the channel plugin so the framework can register it
 *   2. Auto-writes `channels.bcs` to openclaw.json so the next boot loads the full plugin
 */

import { existsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { bcsPlugin } from './channel.js';

function resolveConfigFilePath(): string | null {
  const explicitPath = process.env.OPENCLAW_CONFIG_PATH?.trim();
  if (explicitPath && existsSync(explicitPath)) return explicitPath;

  const stateDir = process.env.OPENCLAW_STATE_DIR?.trim();
  if (stateDir) {
    const candidate = path.join(stateDir, 'openclaw.json');
    if (existsSync(candidate)) return candidate;
  }

  const homedir = process.env.HOME ?? process.env.USERPROFILE ?? '';
  const defaultPath = path.join(homedir, '.openclaw', 'openclaw.json');
  if (existsSync(defaultPath)) return defaultPath;

  return null;
}

/**
 * Write channels.bcs config and trigger gateway restart.
 */
async function ensureBcsConfig(): Promise<void> {
  const configPath = resolveConfigFilePath();
  if (!configPath) {
    console.warn('[BCS setup-entry] Cannot resolve config file path, skipping auto-config');
    return;
  }

  let raw: any;
  try {
    raw = JSON.parse(await readFile(configPath, 'utf-8'));
  } catch (err) {
    console.error('[BCS setup-entry] Failed to read config file:', err);
    return;
  }

  const bcsSection = raw?.channels?.bcs;
  if (bcsSection) {
    const keys = Object.keys(bcsSection).filter(k => k !== 'enabled');
    if (keys.length > 0) {
      return; // has meaningful config beyond just `enabled`, skip to avoid restart
    }
  }

  if (!raw.channels) raw.channels = {};
  if (!raw.channels.bcs) raw.channels.bcs = {};

  raw.channels.bcs.heartbeatIntervalMs = 60000;
  if (raw.channels.bcs.enabled === undefined) {
    raw.channels.bcs.enabled = true;
  }

  await writeFile(configPath, JSON.stringify(raw, null, 2) + '\n', 'utf-8');
  console.info(`[BCS setup-entry] Auto-configured channels.bcs in ${configPath}, manual restart gateway required`);
}

// Fire-and-forget: write config on first load
void ensureBcsConfig().catch(err => {
  console.error('[BCS setup-entry] ensureBcsConfig failed:', err);
});

export default {
  plugin: bcsPlugin,
};
