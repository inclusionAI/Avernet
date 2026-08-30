import type { ComponentType } from 'react';

declare module '@umijs/max' {
  export * from 'umi';
  export const MicroApp: ComponentType<{
    name: string;
    base?: string;
    platform?: string;
    [key: string]: unknown;
  }>;
}
