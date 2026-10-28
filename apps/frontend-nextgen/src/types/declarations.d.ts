declare module 'js-yaml' {
  export function load(text: string, options?: unknown): unknown;
  export function dump(obj: unknown, options?: unknown): string;
}

declare module '@alipay/yuyan-config-data' {
  export enum EnvEnum {
    dev = 'dev',
    pre = 'pre',
    prod = 'prod',
  }
  export const NetworkType: Record<string, unknown>;
  export function fetchData(...args: unknown[]): Promise<unknown>;
}
