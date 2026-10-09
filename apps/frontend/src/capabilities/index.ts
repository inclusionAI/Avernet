/**
 * Capabilities 体系 barrel。
 * 消费方：`import { defineExt, useExt, getExt } from '@/capabilities'`
 * 设计与写法规范见 docs/specs/capabilities-system.md。
 */
export type { Ext, ExtPatch } from './types';
export {
  defineExt,
  extend,
  useExt,
  getExt,
  sealExtensions,
  __resetExtensionsForTest,
} from './core';
