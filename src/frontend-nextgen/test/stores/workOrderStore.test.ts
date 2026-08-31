import type { WorkOrder } from '@/domain/admin/models';
import { useWorkOrderStore } from '@/stores/workOrderStore';

const buildItem = (workOrderId: number, overrides: Partial<WorkOrder> = {}): WorkOrder =>
  ({
    itemId: `WORK_ORDER_${workOrderId}`,
    itemType: 'APPROVAL',
    workOrderId,
    notificationId: workOrderId + 10000,
    bizType: 'SPACE_JOIN',
    bizId: '10001',
    eventType: 'SPACE_JOIN_APPLIED',
    title: '空间加入申请待审批',
    content: '用户「张三」申请加入空间「风控团队」，请及时处理。',
    status: 'PENDING',
    statusLabel: '待审批',
    typeLabel: '加入团队',
    typeTone: 'orange',
    isRead: false,
    canApprove: true,
    gmtModified: '2026-08-12T11:58:00+08:00',
    ...overrides,
  } as WorkOrder);

describe('workOrderStore.markItemRead', () => {
  beforeEach(() => {
    useWorkOrderStore.getState().reset();
  });

  it('只翻中目标的已读态，不移出列表也不改 total（审批类读过仍要留在待我处理）', () => {
    const { setList, markItemRead } = useWorkOrderStore.getState();
    setList([buildItem(1), buildItem(2)], 2);

    markItemRead(1);

    const { items, total } = useWorkOrderStore.getState();
    expect(items).toHaveLength(2);
    expect(total).toBe(2);
    expect(items[0].isRead).toBe(true);
    expect(items[1].isRead).toBe(false);
  });

  it('数字/字符串 id 都能命中（后端 work_order_id 类型不稳定）', () => {
    const { setList, markItemRead } = useWorkOrderStore.getState();
    setList([buildItem(30001)], 1);

    markItemRead('30001');

    expect(useWorkOrderStore.getState().items[0].isRead).toBe(true);
  });

  it('id 不存在时不改动列表', () => {
    const { setList, markItemRead } = useWorkOrderStore.getState();
    setList([buildItem(1)], 1);

    markItemRead(999);

    expect(useWorkOrderStore.getState().items[0].isRead).toBe(false);
  });
});

describe('workOrderStore.removeItem', () => {
  beforeEach(() => {
    useWorkOrderStore.getState().reset();
  });

  it('移出条目并递减 total（通知类读过即移出待我处理）', () => {
    const { setList, removeItem } = useWorkOrderStore.getState();
    setList([buildItem(1, { itemType: 'NOTIFICATION' }), buildItem(2)], 2);

    removeItem(1);

    const { items, total } = useWorkOrderStore.getState();
    expect(items.map((i) => i.workOrderId)).toEqual([2]);
    expect(total).toBe(1);
  });
});
