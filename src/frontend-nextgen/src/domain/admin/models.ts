// 管理后台领域模型（Domain）。纯 TS 类型，不依赖运行时。
// Component/Hook 只认 Domain；DTO ⇄ Domain 映射在 mappers.ts 完成。
// 设计依据：docs/specs/2026-08-17-admin-module/spec.md §3、plan.md §3。
// 分页结构直接复用 backendApi 的 BackendApiPage<T>；Service 返回的 *ListResult 参考 botWorkshop BotListResult。

import type { WorkOrderTone } from './workOrderMeta';

export type SpaceType = 'TEAM' | 'PERSONAL' | 'UNKNOWN';
export type SpaceRole = 'ADMIN' | 'MEMBER' | 'UNKNOWN';
export type WorkOrderStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'UNKNOWN';
export type WorkOrderItemType = 'APPROVAL' | 'NOTIFICATION' | 'UNKNOWN';
/** 工单中心三视图（对应顶部分组 tab） */
export type WorkOrderView = 'pending_mine' | 'initiated_mine' | 'processed';
/** 工单中心分类筛选（Segmented 全部/审批类/通知类） */
export type WorkOrderCategory = 'ALL' | 'APPROVAL' | 'NOTIFICATION';

/** 空间加入态（后端 join_status：JOINED/APPLYING/NOT_JOINED） */
export type SpaceJoinStatus = 'JOINED' | 'APPLYING' | 'NOT_JOINED' | 'UNKNOWN';

export interface Space {
  spaceId: number;
  spaceCode: string;
  spaceName: string;
  spaceType: SpaceType;
  /** 当前用户在该空间的角色；非成员为 undefined */
  currentUserRole?: SpaceRole;
  isCreator?: boolean;
  /** 创建者用户 id（后端 creator_user_id，与 creator_user_name 同级） */
  creatorUserId?: string;
  /** 创建者展示名/花名（后端 creator_user_name；空间卡片「创建者」列直接展示，替换原管理员计数） */
  creatorUserName?: string;
  joinStatus?: SpaceJoinStatus;
  memberCount: number;
  ownerCount: number;
  /** 空间内 Bot 数量 */
  botCount: number;
  gmtCreate?: string;
  gmtModified: string;
}

export interface SpaceMember {
  userId: string;
  userName: string;
  displayName?: string;
  role: SpaceRole;
  /** 该成员拥有管理员权限的 Bot 数量（成员表「权限」列）。 */
  botPermissionCount: number;
  isCreator: boolean;
  gmtModified: string;
}

export interface WorkOrder {
  itemId: string;
  itemType: WorkOrderItemType;
  workOrderId: number;
  workOrderNo?: string;
  notificationId: number;
  notificationCategory?: string;
  bizType: string;
  /** 工单业务 id（契约更新后为字符串，如 "10001"） */
  bizId: string;
  /** 申请人用户 id（"我发起的"=applicant=me） */
  applicantUserId?: string;
  /** 申请人展示名（详情 content.applicant_name；列表不返回，展示时回退 applicantUserId） */
  applicantName?: string;
  /** 申请理由 */
  applyReason?: string;
  /** 审批人用户 id（未处理时为 null/undefined） */
  reviewerUserId?: string;
  /** 审批人展示名（工单详情 VO reviewer_user_name；展示时优先，回退 reviewerUserId） */
  reviewerUserName?: string;
  /** 审批备注 */
  reviewRemark?: string;
  /** 审批时间（ISO，未处理为 null/undefined） */
  reviewedAt?: string;
  /** 收件人用户 id（"待我处理"=recipient=me 的收件箱锚点） */
  recipientUserId?: string;
  eventType: string;
  title: string;
  content: string;
  /** 详情抽屉用的原始 JSON 文本（对象 content 经 JSON.stringify，列表/通知不填充）。 */
  contentRaw?: string;
  status: WorkOrderStatus;
  /** 状态中文 label（mapper 填充，单一源 workOrderMeta） */
  statusLabel: string;
  /** 类型中文 label（mapper 填充） */
  typeLabel: string;
  /** 类型颜色 tone（mapper 填充；组件只消费，不自定） */
  typeTone: WorkOrderTone;
  isRead: boolean;
  /** 阅读时间（ISO，未读为 null/undefined） */
  readAt?: string;
  /** 来源环境标识（如 "pre"） */
  env?: string;
  canApprove: boolean;
  gmtCreated?: string;
  gmtModified: string;
}

/** 铃铛 tooltip 最近 N 条的精简视图 */
export type NotificationSummary = Pick<
  WorkOrder,
  'itemId' | 'title' | 'content' | 'gmtModified' | 'itemType' | 'notificationId' | 'isRead'
>;

/** 工单列表查询参数（Hook/Service 层用） */
export interface WorkOrderListQuery {
  view: WorkOrderView;
  category: WorkOrderCategory;
  page?: number;
  pageSize?: number;
  /** 当前用户 id（保留给未来细粒度过滤；query_type 已由后端按当前用户处理，本期不传 */
  currentUserId?: string;
  keyword?: string;
}

/** 空间列表查询参数 */
export interface SpaceListQuery {
  keyword?: string;
  spaceType?: SpaceType;
  page?: number;
  pageSize?: number;
  /** 仅返回当前账号可访问（已加入）的空间：透传为 query scope=accessible，由后端过滤（替代前端 filterJoinedSpaces）。 */
  scope?: 'accessible';
}

/** 创建团队空间入参 */
export interface CreateTeamSpaceInput {
  spaceName: string;
}

/** 空间成员列表查询参数 */
export interface SpaceMemberListQuery {
  keyword?: string;
  page?: number;
  pageSize?: number;
}

/** Service 返回的工单列表结果（参 botWorkshop BotListResult） */
export interface WorkOrderListResult {
  items: WorkOrder[];
  total?: number;
  page: number;
  pageSize: number;
  hasMore?: boolean;
  warnings: string[];
}

export interface SpaceListResult {
  items: Space[];
  total?: number;
  page: number;
  pageSize: number;
  hasMore?: boolean;
  warnings: string[];
}

export interface SpaceMemberListResult {
  items: SpaceMember[];
  total?: number;
  page: number;
  pageSize: number;
  hasMore?: boolean;
  warnings: string[];
}

/** 审批可用性判定结果（Service.canApprove 返回） */
export interface ApprovalAvailability {
  ok: boolean;
  reason?: string;
}

/** Service 统一错误承载（标准化，不 throw 到 Component） */
export interface ServiceError {
  message: string;
  apiPath: string;
  /** 后端 request_id（用于排障关联；来自信封或 BackendRequestError.data.request_id） */
  requestId?: string;
}

/** 缺失内部能力（identity/网关）时的降级返回标志 */
export interface UnsupportedResult {
  unsupported: true;
  reason: string;
}
