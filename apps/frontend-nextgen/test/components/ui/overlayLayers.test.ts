/** @jest-environment node */
// 浮层层级 Token 的排序约束（global.css）。嵌套浮层（公开设置 Modal → 管理好友
// 抽屉 → 移除确认框）依赖这份顺序：Portal 不会因 React 嵌套抬升层级，只能靠
// Token 保证「抽屉 > 父 Modal、确认框 > 抽屉」。顺序回退会重新出现遮罩挡点击。
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from '@jest/globals';

const css = readFileSync(path.join(process.cwd(), 'src', 'global.css'), 'utf8');

function layer(name: string): number {
  const match = new RegExp(`--${name}:\\s*(\\d+)`).exec(css);
  if (!match) throw new Error(`missing overlay token --${name}`);
  return Number(match[1]);
}

describe('overlay layer tokens', () => {
  it('orders nested overlays above their container and below critical dialogs', () => {
    expect(layer('z-drawer')).toBeLessThan(layer('z-dialog'));
    expect(layer('z-dialog')).toBeLessThan(layer('z-nested-drawer'));
    expect(layer('z-nested-drawer')).toBeLessThan(layer('z-critical-dialog'));
    expect(layer('z-critical-dialog')).toBeLessThan(layer('z-popover'));
    expect(layer('z-popover')).toBeLessThan(layer('z-tooltip'));
    expect(layer('z-tooltip')).toBeLessThan(layer('z-toast'));
  });
});