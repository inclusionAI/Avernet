import { describe, expect, it } from '@jest/globals';
import { appExtension, registerSidePanelWiring } from '@/extensions/collab';
import { getExtensionSlots } from '@/extensions/slots';

describe('collab 装配', () => {
  it('导航覆盖为「协作群 + 外部 Bot」两项', () => {
    const items = appExtension.capabilities.getPrimaryNavigationItems().value;
    expect(items?.map((item) => [item.id, item.path])).toEqual([
      ['collaboration', '/workspace/collaboration'],
      ['external-bots', '/external-bots'],
    ]);
  });

  it('壳层可见性覆盖：隐藏空间切换/通知中心/全局搜索', () => {
    expect(appExtension.capabilities.getShellVisibility().value).toEqual({
      spaceSwitcher: false,
      notificationBell: false,
      globalSearch: false,
    });
    expect(appExtension.capabilities.getSpaceContextInitializationEnabled().value).toBe(false);
  });

  it('槽位整体为 null（任务栈/TC 选路不进 collab 产物）', () => {
    expect(getExtensionSlots()).toEqual({
      groupTasks: null,
      groupServiceBotRouting: null,
      taskSessionNavigationObserver: null,
      taskPanelBootstrap: null,
    });
    expect(appExtension.avatarEditor).toBeNull();
  });

  it('registerSidePanelWiring 为可调用函数（UMD 轨保留，任务轨缺席）', () => {
    expect(typeof registerSidePanelWiring).toBe('function');
  });
});
