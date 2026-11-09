import { describe, expect, it } from '@jest/globals';
import { getExtensionSlots, registerExtensionSlots } from '@/extensions/slots';

describe('extension slots registry', () => {
  it('jest 全局装配提供非空默认槽位（占位/桩件，对齐 app.tsx 启动槽位非空的形状）', () => {
    const slots = getExtensionSlots();
    expect(slots.groupTasks).not.toBeNull();
    expect(slots.groupServiceBotRouting).not.toBeNull();
    expect(slots.taskSessionNavigationObserver).not.toBeNull();
    expect(slots.taskPanelBootstrap).not.toBeNull();
  });

  it('可整体覆盖为 null（collab 形态），组件解析到空槽位', () => {
    registerExtensionSlots({ groupTasks: null, groupServiceBotRouting: null, taskSessionNavigationObserver: null, taskPanelBootstrap: null, sessionTaskPanelTab: null });
    expect(getExtensionSlots().groupTasks).toBeNull();
  });
});
