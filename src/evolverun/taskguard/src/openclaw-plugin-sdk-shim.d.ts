declare module "openclaw/plugin-sdk/sandbox" {
  export function runSandboxed(...args: any[]): any;
  export function createSandbox(...args: any[]): any;
  export function runPluginCommandWithTimeout(...args: any[]): any;
  const _default: any;
  export default _default;
}

declare module "openclaw/plugin-sdk/plugin-entry" {
  export interface PluginEntryConfig {
    id: string;
    name: string;
    description: string;
    register: (api: PluginApi) => void;
  }

  export interface PluginApi {
    runtime?: any;
    registerTool?(...args: any[]): any;
    on?(...args: any[]): any;
    [k: string]: any;
  }

  export function definePluginEntry(config: PluginEntryConfig): any;
  const _default: any;
  export default _default;
}
