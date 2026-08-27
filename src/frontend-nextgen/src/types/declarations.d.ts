declare module 'js-yaml' {
  export function load(text: string, options?: unknown): unknown;
  export function dump(obj: unknown, options?: unknown): string;
}

declare module '@xyflow/react' {
  import type * as React from 'react';

  export type Edge<T = unknown> = {
    id: string;
    source: string;
    target: string;
    animated?: boolean;
    label?: string;
    style?: React.CSSProperties;
  } & T;

  export type Node<T = unknown> = {
    id: string;
    position: { x: number; y: number };
    data: T;
    type?: string;
  };

  export type NodeProps<T = unknown> = T extends Node<infer D> ? { data: D } : { data: T };

  export const ReactFlow: React.FC<Record<string, unknown>>;
  export const Background: React.FC<Record<string, unknown>>;
  export const Controls: React.FC<Record<string, unknown>>;
  export const Handle: React.FC<Record<string, unknown>>;
  export enum Position {
    Top = 'top',
    Bottom = 'bottom',
    Left = 'left',
    Right = 'right',
  }
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
