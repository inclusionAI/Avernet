import { useBotWorkshopStore } from '@/stores/botWorkshopStore';

describe('botWorkshopStore', () => {
  afterEach(() => useBotWorkshopStore.getState().reset());

  test('筛选状态可更新并重置', () => {
    useBotWorkshopStore.getState().setKeyword('demo');
    useBotWorkshopStore.getState().setEngine('openclaw');
    useBotWorkshopStore.getState().setPage(3);

    expect(useBotWorkshopStore.getState().keyword).toBe('demo');
    expect(useBotWorkshopStore.getState().engine).toBe('openclaw');
    expect(useBotWorkshopStore.getState().page).toBe(3);

    useBotWorkshopStore.getState().reset();
    expect(useBotWorkshopStore.getState().page).toBe(1);
    expect(useBotWorkshopStore.getState().keyword).toBe('');
  });
});
