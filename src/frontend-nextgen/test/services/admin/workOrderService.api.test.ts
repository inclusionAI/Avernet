/** @jest-environment node */
import { workOrderService } from '@/services/admin/workOrderService';
import * as notificationController from '@/services/backendApi/admin/notificationController';
import * as workOrderController from '@/services/backendApi/admin/workOrderController';
import { identityService } from '@/services/workspace/identityService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/admin/workOrderController');
jest.mock('@/services/backendApi/admin/notificationController');
// ensureUserId 在 activeIdentityId 未就绪时补拉 identityService.loadIdentities；
// stub @tc-chat/adapters ESM transitive（identityService→testUser→supportProvider）。
jest.mock('@/services/workspace/identityService');
jest.mock('@tc-chat/adapters', () => ({}));

const wc = workOrderController as unknown as Record<string, jest.Mock<any>>;
// notificationController 被 mock 以隔离 markNotificationRead（本文件不测它）。
void (notificationController as unknown as Record<string, jest.Mock<any>>);

beforeEach(() => {
  jest.resetAllMocks();
  useWorkspaceStore.setState({ activeIdentityId: 'human_327325' });
  // 命中缓存用例不调 loadIdentities；未就绪用例走 ensureUserId 补拉，默认失败降级为 error（不发业务请求）。
  (identityService.loadIdentities as unknown as jest.Mock<any>).mockResolvedValue({
    ok: false,
    error: { code: 'IDENTITY_LOAD_FAILED', friendlyMessage: '', canRetry: true },
  });
});

describe('workOrderService.list 参数对齐 clawweb=Avernet', () => {
  it('initiated_mine + NOTIFICATION → INITIATED_BY_ME + NOTICE，page→page_no，注入 user_id', async () => {
    wc.listWorkOrders.mockResolvedValue({ success: true, data: { items: [], total: 0 } });
    await workOrderService.list({ view: 'initiated_mine', category: 'NOTIFICATION', page: 2, pageSize: 10 });
    expect(wc.listWorkOrders).toHaveBeenCalledWith({
      user_id: '327325',
      page_no: 2,
      page_size: 10,
      query_type: 'INITIATED_BY_ME',
      item_type: 'NOTICE',
    });
  });

  it('pending_mine + APPROVAL → PENDING_FOR_ME + APPROVAL；默认 page_no=1/page_size=20', async () => {
    wc.listWorkOrders.mockResolvedValue({ success: true, data: { items: [], total: 0 } });
    await workOrderService.list({ view: 'pending_mine', category: 'APPROVAL' });
    expect(wc.listWorkOrders).toHaveBeenCalledWith(
      expect.objectContaining({
        query_type: 'PENDING_FOR_ME',
        item_type: 'APPROVAL',
        page_no: 1,
        page_size: 20,
        user_id: '327325',
      }),
    );
  });

  it('ALL 分类 → item_type=ALL', async () => {
    wc.listWorkOrders.mockResolvedValue({ success: true, data: { items: [], total: 0 } });
    await workOrderService.list({ view: 'processed', category: 'ALL' });
    expect(wc.listWorkOrders).toHaveBeenCalledWith(
      expect.objectContaining({ query_type: 'PROCESSED_BY_ME', item_type: 'ALL' }),
    );
  });

  it('activeIdentityId 未就绪时返回 error 不发请求', async () => {
    useWorkspaceStore.setState({ activeIdentityId: null });
    const r = await workOrderService.list({ view: 'pending_mine', category: 'ALL' });
    expect(r.error).toBeDefined();
    expect(wc.listWorkOrders).not.toHaveBeenCalled();
  });
});

describe('workOrderService.approve / reject → 统一审批入口', () => {
  it('approve 未给 remark → decision=APPROVED, review_remark=null + user_id', async () => {
    wc.submitWorkOrderApproval.mockResolvedValue({ success: true, data: {} });
    await workOrderService.approve(30001);
    expect(wc.submitWorkOrderApproval).toHaveBeenCalledWith(
      30001,
      { decision: 'APPROVED', review_remark: null },
      { user_id: '327325' },
    );
  });

  it('approve 给 remark → decision=APPROVED, review_remark=trim 后值', async () => {
    wc.submitWorkOrderApproval.mockResolvedValue({ success: true, data: {} });
    await workOrderService.approve(30001, ' 同意 ');
    expect(wc.submitWorkOrderApproval).toHaveBeenCalledWith(
      30001,
      { decision: 'APPROVED', review_remark: '同意' },
      { user_id: '327325' },
    );
  });

  it('reject 传 review_remark（必填）→ decision=REJECTED + user_id', async () => {
    wc.submitWorkOrderApproval.mockResolvedValue({ success: true, data: {} });
    await workOrderService.reject(30001, '理由不充分');
    expect(wc.submitWorkOrderApproval).toHaveBeenCalledWith(
      30001,
      { decision: 'REJECTED', review_remark: '理由不充分' },
      { user_id: '327325' },
    );
  });

  it('approve 传 user_name(花名) query（同 requestJoin 契约，审批人随 user_id 写入工单）', async () => {
    // 注入 identities 使 ensureUserName 经 getHumanIdentity 命中花名缓存（不调 loadIdentities）。
    useWorkspaceStore.setState({
      activeIdentityId: 'human_327325',
      identities: [{ id: 'human_327325', kind: 'user', displayName: '风太', online: true }],
    });
    wc.submitWorkOrderApproval.mockResolvedValue({ success: true, data: {} });
    await workOrderService.approve(30001);
    expect(wc.submitWorkOrderApproval).toHaveBeenCalledWith(
      30001,
      { decision: 'APPROVED', review_remark: null },
      { user_id: '327325', user_name: '风太' },
    );
  });

  it('getDetail 注入 user_id', async () => {
    wc.getWorkOrderDetail.mockResolvedValue({ success: true, data: { work_order_id: 30001, status: 'PENDING' } });
    await workOrderService.getDetail(30001);
    expect(wc.getWorkOrderDetail).toHaveBeenCalledWith(30001, { user_id: '327325' });
  });
});
