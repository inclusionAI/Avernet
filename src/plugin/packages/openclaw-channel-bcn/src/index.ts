import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/core';
import { emptyPluginConfigSchema } from 'openclaw/plugin-sdk/core';
import { registerBcsCore } from './core.js';

const plugin = {
  id: 'openclaw-channel-bcn',
  name: 'BCS',
  description: 'BCS multi-bot collaboration channel plugin for OpenClaw',
  configSchema: emptyPluginConfigSchema(),
  register(api: OpenClawPluginApi) {
    console.log('[openclaw-channel-bcn] plugin registered');
    registerBcsCore(api);
  },
};

export default plugin;
export { bcsPlugin, createBcsPlugin } from './channel.js';
export { setBcsRuntime } from './runtime.js';
export { getDefaultBcsUrl, listAccountIds, resolveAccount } from './accounts.js';
export { BCS_CORE_TOOL_NAMES, registerBcsCore } from './core.js';
export { resolveGroupIdFromSessionKey } from './inbound-handler.js';
export type { BcsCoreRegistration } from './core.js';
export type { ResolvedBcsAccount } from './types.js';
