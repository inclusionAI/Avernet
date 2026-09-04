import {
  mapSpaceDto,
  mapSpaceList,
  mapSpaceMemberDto,
  mapSpaceMemberList,
  mapWorkOrderDto,
  mapWorkOrderList,
} from '@/domain/admin/mappers';
import type { BackendApiPage, BackendUnknownRecord } from '@/services/backendApi/types';
import { describe, expect, it } from '@jest/globals';

const workOrderDto: BackendUnknownRecord = {
  item_id: 'WORK_ORDER_30001',
  item_type: 'APPROVAL',
  work_order_id: 30001,
  work_order_no: 'WO202608120001',
  notification_id: 40001,
  notification_category: 'APPROVAL',
  biz_type: 'SPACE_JOIN',
  biz_id: '10001',
  applicant_user_id: '2088123456789012',
  apply_reason: '申请加入空间参与 Skill 建设',
  reviewer_user_id: null,
  review_remark: null,
  reviewed_at: null,
  recipient_user_id: '2088123456789001',
  event_type: 'SPACE_JOIN_APPLIED',
  title: '空间加入申请待审批',
  content: '用户「张三」申请加入空间「风控团队」',
  status: 'PENDING',
  is_read: false,
  read_at: null,
  env: 'pre',
  can_approve: true,
  gmt_created: '2026-08-12T11:50:00+08:00',
  gmt_modified: '2026-08-12T11:58:00+08:00',
};

const spaceDto: BackendUnknownRecord = {
  space_id: 10001,
  space_code: 'space_a8f93c21',
  space_name: '风控团队',
  space_type: 'TEAM',
  creator_user_id: '435979',
  creator_user_name: '拓界',
  current_user_role: 'ADMIN',
  is_creator: true,
  join_status: 'JOINED',
  member_count: 6,
  owner_count: 2,
  gmt_modified: '2026-08-12T11:58:00+08:00',
};

const memberDto: BackendUnknownRecord = {
  user_id: '2088123456789013',
  user_name: '张三',
  display_name: '张三',
  role: 'ADMIN',
  is_creator: true,
  gmt_modified: '2026-08-12T11:58:00+08:00',
};

describe('mapWorkOrderDto', () => {
  it('maps a complete DTO to Domain with renamed fields incl. contract-update additions', () => {
    const { item, warnings } = mapWorkOrderDto(workOrderDto);
    expect(item.itemId).toBe('WORK_ORDER_30001');
    expect(item.itemType).toBe('APPROVAL');
    expect(item.workOrderId).toBe(30001);
    expect(item.workOrderNo).toBe('WO202608120001');
    expect(item.notificationId).toBe(40001);
    expect(item.notificationCategory).toBe('APPROVAL');
    expect(item.bizId).toBe('10001'); // 字符串
    expect(item.applicantUserId).toBe('2088123456789012');
    expect(item.applyReason).toContain('Skill');
    expect(item.recipientUserId).toBe('2088123456789001');
    expect(item.reviewerUserId).toBeUndefined(); // null
    expect(item.reviewedAt).toBeUndefined();
    expect(item.readAt).toBeUndefined();
    expect(item.env).toBe('pre');
    expect(item.gmtCreated).toContain('2026-08-12');
    expect(item.status).toBe('PENDING');
    expect(item.isRead).toBe(false);
    expect(item.canApprove).toBe(true);
    expect(item.gmtModified).toContain('2026-08-12');
    expect(item.typeLabel).toBe('申请加入团队');
    expect(item.typeTone).toBe('orange');
    expect(item.statusLabel).toBe('待审批');
    expect(warnings).toHaveLength(0);
  });

  it('通知类二分 typeLabel/typeTone：审批通知=绿 / 通知=蓝（PRD notifTypeLabel）', () => {
    const approvalNotice = mapWorkOrderDto({
      ...workOrderDto,
      item_type: 'NOTIFICATION',
      notification_category: 'APPROVAL',
    }).item;
    expect(approvalNotice.typeTone).toBe('green');
    expect(approvalNotice.typeLabel).toBe('审批通知');
    const plainNotice = mapWorkOrderDto({
      ...workOrderDto,
      event_type: 'SPACE_MEMBER_ADDED',
      item_type: 'NOTIFICATION',
      notification_category: 'NOTICE',
    }).item;
    expect(plainNotice.typeTone).toBe('blue');
    expect(plainNotice.typeLabel).toBe('通知');
  });

  it('审批类 typeLabel 对齐 PRD：好友申请=绿', () => {
    const friend = mapWorkOrderDto({ ...workOrderDto, event_type: 'HUMAN2BOT_FRIEND_APPLIED' }).item;
    expect(friend.typeTone).toBe('green');
    expect(friend.typeLabel).toBe('好友申请');
  });

  it('maps legacy numeric biz_id to string for back-compat', () => {
    const { item } = mapWorkOrderDto({ work_order_id: 1, biz_id: 10001 });
    expect(item.bizId).toBe('10001');
  });

  it('falls back to defaults on null/missing fields without throwing', () => {
    const { item } = mapWorkOrderDto({});
    expect(item.itemId).toBe('');
    expect(item.workOrderId).toBe(0);
    expect(item.itemType).toBe('UNKNOWN');
    expect(item.status).toBe('UNKNOWN');
    expect(item.isRead).toBe(false);
    // 空 content → contentRaw 不填充
    expect(item.contentRaw).toBeUndefined();
  });

  it('maps unknown enum values to UNKNOWN and records warning', () => {
    const { item, warnings } = mapWorkOrderDto({ item_type: 'WHATEVER', status: 'WIP' });
    expect(item.itemType).toBe('UNKNOWN');
    expect(item.status).toBe('UNKNOWN');
    expect(warnings.join()).toContain('item_type');
    expect(warnings.join()).toContain('status');
  });

  it('maps backend item_type NOTICE to model NOTIFICATION（契约 APPROVAL/NOTICE）', () => {
    const { item } = mapWorkOrderDto({ ...workOrderDto, item_type: 'NOTICE' });
    expect(item.itemType).toBe('NOTIFICATION');
  });

  it('详情 content 为对象时：按 content 解析 applicant/applyReason，合成 content 文案，按 event_type 推断 itemType', () => {
    // 真实详情 VO（GET work-orders/{id}）：无 item_type，apply_reason/applicant_* 藏在 content 内
    const { item, warnings } = mapWorkOrderDto({
      work_order_id: 5,
      work_order_no: 'WO20260821141951F363F34363',
      biz_type: 'SPACE_JOIN',
      biz_id: 6,
      event_type: 'SPACE_JOIN_APPLIED',
      title: '空间加入申请待审批',
      content: {
        space_id: 6,
        space_name: '测试空间2',
        applicant_user_id: '146836',
        applicant_name: '146836',
        reason: 'test',
      },
      status: 'PENDING',
      can_approve: false,
    });
    // 无 item_type 但有 event_type → 推断为 APPROVAL，且不误报「未知 item_type」
    expect(item.itemType).toBe('APPROVAL');
    expect(warnings.join()).not.toContain('item_type');
    expect(item.typeLabel).toBe('申请加入团队');
    expect(item.typeTone).toBe('orange');
    // applicant_* / apply_reason 来自 content 对象
    expect(item.applicantUserId).toBe('146836');
    expect(item.applicantName).toBe('146836');
    expect(item.applyReason).toBe('test');
    // content 合成为一行展示文案（不重复申请人）；列表文案 content 不变
    expect(item.content).toBe('申请加入空间「测试空间2」');
    // 详情 content 为对象 → 抽屉改 JSON 原样展示：contentRaw = pretty JSON
    expect(item.contentRaw).toBe(
      JSON.stringify(
        {
          space_id: 6,
          space_name: '测试空间2',
          applicant_user_id: '146836',
          applicant_name: '146836',
          reason: 'test',
        },
        null,
        2,
      ),
    );
    expect(item.status).toBe('PENDING');
    expect(item.canApprove).toBe(false);
  });

  it('列表 content 为字符串时原样透传、不触发对象解析', () => {
    const { item } = mapWorkOrderDto({ ...workOrderDto });
    expect(item.content).toBe('用户「张三」申请加入空间「风控团队」');
    // 字符串 content（列表 VO）不进对象分支 → contentRaw 不填充
    expect(item.contentRaw).toBeUndefined();
    expect(item.applicantName).toBeUndefined();
    expect(item.applyReason).toBe('申请加入空间参与 Skill 建设');
  });

  it('通知详情 VO：content 为 { legacy_value } 透传文案、work_order_status 字段名、无 item_type 按 event_type 推断', () => {
    // 真实通知详情（GET work-order-notifications/{id}）线上 payload：content 包成 { legacy_value }，
    // 状态字段名为 work_order_status（非 status），无 item_type。复刻以锁契约。
    const { item, warnings } = mapWorkOrderDto({
      notification_id: 30,
      work_order_id: 16,
      notification_category: 'NOTICE',
      event_type: 'SPACE_JOIN_REVIEWED',
      title: '空间加入申请已通过',
      content: { legacy_value: '你加入空间「系统智能」的申请已通过。' },
      is_read: true,
      work_order_status: 'APPROVED',
      can_approve: false,
      biz_type: 'SPACE_JOIN',
      biz_id: '8',
    });
    // legacy_value 已是后端合成好的展示文案，直接透传（不再走到 space_name/reason 分支返回空）
    expect(item.content).toBe('你加入空间「系统智能」的申请已通过。');
    // content 为 { legacy_value } 对象 → contentRaw = pretty JSON（抽屉 JSON 展示）
    expect(item.contentRaw).toBe(JSON.stringify({ legacy_value: '你加入空间「系统智能」的申请已通过。' }, null, 2));
    // 详情 VO 用 work_order_status，mapper 需识别（旧实现只读 status → UNKNOWN）
    expect(item.status).toBe('APPROVED');
    expect(item.statusLabel).toBe('已通过');
    // 无 item_type + event_type=SPACE_JOIN_REVIEWED(NOTICE) → 推断 NOTIFICATION，不误报 item_type
    expect(item.itemType).toBe('NOTIFICATION');
    expect(warnings.join()).not.toContain('item_type');
    expect(item.notificationId).toBe(30);
    expect(item.workOrderId).toBe(16);
    expect(item.bizType).toBe('SPACE_JOIN');
    expect(item.bizId).toBe('8');
    expect(item.isRead).toBe(true);
    expect(item.canApprove).toBe(false);
  });

  it('顶层 summary 优先生效：列表文案从 content.legacy_value 切到 summary（与 content 同级）', () => {
    // 线上列表 payload（APPROVAL 工单）：content 仍包 { legacy_value }，新契约把展示文案提升为顶层 summary
    const { item } = mapWorkOrderDto({
      ...workOrderDto,
      item_id: 'NOTIFICATION_6',
      work_order_id: 5,
      notification_id: 6,
      title: '空间加入申请待审批',
      summary: '有新的空间加入申请，请及时处理。',
      content: { legacy_value: '用户「146836」申请加入空间「测试空间2」，请及时处理。' },
    });
    // summary 是与 content 同级的展示文案，替代 legacy_value
    expect(item.content).toBe('有新的空间加入申请，请及时处理。');
    expect(item.applicantName).toBeUndefined();
  });

  it('无顶层 summary 时回退既有合成链：legacy_value / 字符串 content 不受影响', () => {
    const legacy = mapWorkOrderDto({
      title: '空间加入申请已通过',
      content: { legacy_value: '你加入空间「系统智能」的申请已通过。' },
    }).item;
    expect(legacy.content).toBe('你加入空间「系统智能」的申请已通过。');
  });

  it('工单详情 reviewer_user_name 映射为 reviewerUserName（展示名），null 归 undefined', () => {
    const reviewed = mapWorkOrderDto({
      work_order_id: 5,
      event_type: 'SPACE_JOIN_APPLIED',
      reviewer_user_id: '12345',
      reviewer_user_name: '拓界',
    }).item;
    expect(reviewed.reviewerUserName).toBe('拓界');
    const pending = mapWorkOrderDto({ reviewer_user_id: null, reviewer_user_name: null }).item;
    expect(pending.reviewerUserName).toBeUndefined();
    expect(pending.reviewerUserId).toBeUndefined();
  });

  it('work_order_status 与 status 两种契约字段名都识别（详情用前者、列表用后者）', () => {
    expect(mapWorkOrderDto({ work_order_status: 'REJECTED' }).item.status).toBe('REJECTED');
    expect(mapWorkOrderDto({ status: 'PENDING' }).item.status).toBe('PENDING');
    // 两者同在时 work_order_status 优先（详情 VO 语义）
    expect(mapWorkOrderDto({ work_order_status: 'APPROVED', status: 'PENDING' }).item.status).toBe('APPROVED');
  });
});

describe('mapSpaceDto', () => {
  it('maps a team space with admin role', () => {
    const { item } = mapSpaceDto(spaceDto);
    expect(item.spaceType).toBe('TEAM');
    expect(item.spaceName).toBe('风控团队');
    expect(item.currentUserRole).toBe('ADMIN');
    expect(item.isCreator).toBe(true);
    expect(item.joinStatus).toBe('JOINED');
    expect(item.memberCount).toBe(6);
    expect(item.creatorUserId).toBe('435979');
    expect(item.creatorUserName).toBe('拓界');
  });

  it('maps creator_user_name / creator_user_id（列表「创建者」列展示花名，替换原管理员计数）', () => {
    const { item } = mapSpaceDto({ ...spaceDto, creator_user_name: '峰癫' });
    expect(item.creatorUserName).toBe('峰癫');
    expect(item.creatorUserId).toBe('435979');
  });

  it('creator_user_name 缺失时为 undefined（卡片 fallback 展示「-」，无需条件判断）', () => {
    const { item } = mapSpaceDto({ space_id: 1, space_name: 'x', space_type: 'TEAM' });
    expect(item.creatorUserName).toBeUndefined();
    expect(item.creatorUserId).toBeUndefined();
  });

  it('leaves currentUserRole undefined when not a member', () => {
    const { item } = mapSpaceDto({ space_id: 1, space_name: '反洗钱团队', space_type: 'TEAM' });
    expect(item.currentUserRole).toBeUndefined();
    expect(item.joinStatus).toBeUndefined();
  });

  it('records warning for unknown space type', () => {
    const { item, warnings } = mapSpaceDto({ space_id: 1, space_name: 'x', space_type: 'WHATEVER' });
    expect(item.spaceType).toBe('UNKNOWN');
    expect(warnings.join()).toContain('空间类型');
  });

  it('maps join_status NOT_JOINED / APPLYING', () => {
    expect(mapSpaceDto({ join_status: 'NOT_JOINED' }).item.joinStatus).toBe('NOT_JOINED');
    expect(mapSpaceDto({ join_status: 'APPLYING' }).item.joinStatus).toBe('APPLYING');
  });
});

describe('mapSpaceMemberDto', () => {
  it('maps member with admin role', () => {
    const { item } = mapSpaceMemberDto(memberDto);
    expect(item.userId).toBe('2088123456789013');
    expect(item.role).toBe('ADMIN');
    expect(item.isCreator).toBe(true);
  });

  it('falls back userName to user_id when user_name missing', () => {
    const { item } = mapSpaceMemberDto({ user_id: 'u1' });
    expect(item.userName).toBe('u1');
    expect(item.role).toBe('UNKNOWN');
  });
});

describe('list mappers', () => {
  const page: BackendApiPage<BackendUnknownRecord> = {
    items: [workOrderDto],
    total: 1,
    page: 1,
    pageSize: 20,
    hasMore: false,
  };

  it('mapWorkOrderList reads BackendApiPage shape (items/total/page/pageSize)', () => {
    const r = mapWorkOrderList(page);
    expect(r.items).toHaveLength(1);
    expect(r.items[0].itemId).toBe('WORK_ORDER_30001');
    expect(r.total).toBe(1);
    expect(r.page).toBe(1);
    expect(r.pageSize).toBe(20);
    expect(r.hasMore).toBe(false);
  });

  it('mapSpaceList and mapSpaceMemberList share the same page contract', () => {
    const sp = mapSpaceList({ items: [spaceDto], total: 1, page: 2, pageSize: 10 });
    expect(sp.items[0].spaceName).toBe('风控团队');
    expect(sp.page).toBe(2);
    expect(sp.pageSize).toBe(10);

    const mp = mapSpaceMemberList({ items: [memberDto], total: 1, page: 1, pageSize: 20 });
    expect(mp.items[0].userId).toBe('2088123456789013');
  });

  it('handles empty/undefined page', () => {
    expect(mapWorkOrderList().items).toEqual([]);
    expect(mapWorkOrderList({ items: [] }).items).toEqual([]);
  });

  it('tolerates contract paging shape { list, page_no, page_size } (real-backend fallback)', () => {
    const r = mapWorkOrderList({
      items: [workOrderDto],
      list: [workOrderDto],
      total: 1,
      page_no: 2,
      page_size: 15,
    } as unknown as Parameters<typeof mapWorkOrderList>[0]);
    expect(r.items).toHaveLength(1);
    expect(r.items[0].workOrderId).toBe(30001);
    expect(r.page).toBe(2);
    expect(r.pageSize).toBe(15);
  });
});
