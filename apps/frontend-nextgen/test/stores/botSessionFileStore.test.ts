/** @jest-environment jsdom */
import { useBotSessionFileStore } from '@/stores/botSessionFileStore';
import { beforeEach, describe, expect, it } from '@jest/globals';

describe('botSessionFileStore（单聊会话文件 store）', () => {
  beforeEach(() => {
    // resetForSession 保留 isLoadingList（PR#413 跟进 P3），测试间显式归零避免串扰。
    useBotSessionFileStore.getState().resetForSession();
    useBotSessionFileStore.setState({ isLoadingList: false });
  });

  it('resetForSession 清空列表与上传任务，但保留在途加载标志', () => {
    const store = useBotSessionFileStore.getState();
    store.setIsLoadingList(true);
    store.setReadyFiles([{ resourceId: 'r1', displayName: 'a.json', status: 'ready', sizeBytes: 1, errorCode: null }]);
    store.addTask({
      localId: 't1',
      name: 'b.png',
      size: 2,
      phase: 'staged',
      progress: 0,
      file: new File([], 'b.png'),
    });

    useBotSessionFileStore.getState().resetForSession();

    const next = useBotSessionFileStore.getState();
    expect(next.readyFiles).toEqual([]);
    expect(next.uploadTasks).toEqual([]);
    expect(next.isUploading).toBe(false);
    // PR#413 评审跟进 P3（#257104460）：切会话时序为 refresh 先置 loading=true，随后 reset
    // 清空旧列表，响应回来再关 loading。若 reset 连 loading 一起清，在途期间列表为空且
    // loading=false，命中 Empty 空态而非 Skeleton（短暂闪「暂无会话文件」）。故 reset 保留
    // isLoadingList，loading 生命周期完全由 refresh 收尾管理。
    expect(next.isLoadingList).toBe(true);
  });
});
