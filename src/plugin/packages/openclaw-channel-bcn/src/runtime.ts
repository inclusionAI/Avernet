import { createPluginRuntimeStore } from 'openclaw/plugin-sdk/runtime-store';
import type { PluginRuntime } from './api.js';

const { setRuntime: setBcsRuntime, getRuntime: getBcsRuntime } =
    createPluginRuntimeStore<PluginRuntime>(
      'BCS runtime not initialized - plugin not registered',
    );
export { getBcsRuntime, setBcsRuntime };
