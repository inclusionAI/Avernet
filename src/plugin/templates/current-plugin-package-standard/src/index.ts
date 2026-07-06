import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/core';

export interface ProcessMessageOptions {
  prefix?: string;
  trim?: boolean;
}

export function processMessage(
  message: string,
  options: ProcessMessageOptions = {},
): string {
  const normalized = options.trim === false ? message : message.trim();
  return options.prefix ? `${options.prefix}${normalized}` : normalized;
}

export default function register(_api: OpenClawPluginApi) {
  // Register tools, commands, routes, or services here.
}
