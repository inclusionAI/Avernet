/**
 * @jest-environment jsdom
 */
// 副屏拖拽宽度记忆 —— SDK 语义补偿:
// tab.defaultWidth 优先于容器级拖宽(userResizedPanelWidth),且带 defaultWidth 的 tab
// 拖拽仅"当前 tab 本次停留"有效,切任务/新开 tab 回落初始宽 → 用户拖过的宽度丢失(体感变窄)。
// 本模块观察 Root 注入的拖拽结果持久化,openTab 时恢复记忆宽;无记忆回落 PRD 初始宽 420。
import { beforeEach, describe, expect, it } from '@jest/globals';

import {
  PANEL_WIDTH_STORAGE_KEY,
  persistPanelWidthPercent,
  readPersistedPanelWidthPx,
  resolveTaskPanelDefaultWidth,
  TASK_PANEL_INITIAL_WIDTH,
} from '@/services/workspace/panelWidthMemory';

describe('panelWidthMemory(副屏拖拽宽度记忆)', () => {
  beforeEach(() => localStorage.clear());

  it('无记忆时 resolveTaskPanelDefaultWidth 回落初始宽(PRD 默认 420,spec §3.1-2)', () => {
    expect(readPersistedPanelWidthPx()).toBeNull();
    expect(TASK_PANEL_INITIAL_WIDTH).toBe(420);
    expect(resolveTaskPanelDefaultWidth()).toBe(420);
  });

  it('persistPanelWidthPercent 把 Root 注入的拖拽百分比按容器宽换算 px 落盘', () => {
    persistPanelWidthPercent('45%', 1000);
    expect(readPersistedPanelWidthPx()).toBe(450);
  });

  it('换算结果钳制在 PRD 拖拽范围 320–720(SDK 窄容器可拖出界外值)', () => {
    persistPanelWidthPercent('5%', 1000); // 50px → 钳到下界 320
    expect(readPersistedPanelWidthPx()).toBe(320);
    persistPanelWidthPercent('90%', 1000); // 900px → 钳到上界 720
    expect(readPersistedPanelWidthPx()).toBe(720);
  });

  it('有记忆宽时 resolveTaskPanelDefaultWidth 恢复记忆值,切任务不回落 420', () => {
    persistPanelWidthPercent('56%', 1000);
    expect(resolveTaskPanelDefaultWidth()).toBe(560);
  });

  it('用户拖窄(如 350)同样被记忆,不强制抬回 420', () => {
    persistPanelWidthPercent('35%', 1000);
    expect(resolveTaskPanelDefaultWidth()).toBe(350);
  });

  it('非法输入:百分比/容器宽无效时忽略,不清掉既有记忆', () => {
    persistPanelWidthPercent('45%', 1000);
    persistPanelWidthPercent('abc', 1000);
    persistPanelWidthPercent('45%', Number.NaN);
    persistPanelWidthPercent('0%', 1000);
    expect(readPersistedPanelWidthPx()).toBe(450);
  });

  it('存储垃圾值时读取按无记忆处理(不抛异常)', () => {
    localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, 'not-a-number');
    expect(readPersistedPanelWidthPx()).toBeNull();
    expect(resolveTaskPanelDefaultWidth()).toBe(420);
  });
});