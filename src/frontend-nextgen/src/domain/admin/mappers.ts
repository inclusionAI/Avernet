// DTO ⇄ Domain 映射（管理后台）。纯函数，无副作用。
// 参考实现：src/services/botWorkshop/botMapper.ts 的 mapBotDto / mapBotList。
// 分页响应统一按通用 BackendApiPage<T> 读取（page.items/total/page/pageSize/hasMore）。

import type { BackendApiPage, BackendUnknownRecord } from '@/services/backendApi/types';
import type {
  Space,
  SpaceJoinStatus,
  SpaceListResult,
  SpaceMember,
  SpaceMemberListResult,
  SpaceRole,
  SpaceType,
  WorkOrder,
  WorkOrderItemType,
  WorkOrderListResult,
  WorkOrderStatus,
} from './models';
import { noticeTypeMeta, resolveWorkOrderType, WORK_ORDER_STATUS_LABEL, WORK_ORDER_TYPE_META } from './workOrderMeta';

const asString = (value: unknown) => (typeof value === 'string' && value.trim() ? value.trim() : undefined);
const asNumber = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : undefined);
// 严格 bool：仅 `true`/`false` 命中；null / undefined / 字符串 / 数字 全部返回 undefined，
// 让 model.isRead?: boolean 表达"未读状态未知"，避免把 approval-only 工单(null) 误标为未读。
const asBool = (value: unknown) => (typeof value === 'boolean' ? value : undefined);

const UNKNOWN_SUFFIX = 'UNKNOWN' as const;

/**
 * 工单详情 content 为结构化对象（SPACE_JOIN 形态：space_id/space_name/applicant_user_id/
 * applicant_name/reason）；列表/通知 content 已是字符串，原样透传不走此处。
 * 把对象合成一行展示文案——只给「动作 + 空间」，申请人/理由交给 drawer 的 dl 行，避免重复。
 */
function synthesizeContentDisplay(contentObj: BackendUnknownRecord): string {
  // NOTICE 类详情（如 SPACE_JOIN_REVIEWED）content 形如 { legacy_value: "你加入空间「X」的申请已通过。" }，
  // legacy_value 已是后端合成好的展示文案，直接透传，避免落到 space_name/reason 分支返回空串。
  // 注：新契约下展示文案优先取顶层 summary（见 mapWorkOrderDto），legacy_value 仅作旧 payload 兜底。
  const legacy = asString(contentObj.legacy_value);
  if (legacy) return legacy;
  const spaceName = asString(contentObj.space_name);
  if (spaceName) return `申请加入空间「${spaceName}」`;
  return asString(contentObj.reason) ?? '';
}

function spaceTypeFrom(dto: BackendUnknownRecord): SpaceType {
  const v = asString(dto.space_type)?.toUpperCase();
  if (v === 'TEAM') return 'TEAM';
  if (v === 'PERSONAL') return 'PERSONAL';
  return UNKNOWN_SUFFIX as SpaceType;
}

function spaceRoleFrom(dto: BackendUnknownRecord, key = 'current_user_role'): SpaceRole {
  const v = asString((dto as Record<string, unknown>)[key])?.toUpperCase();
  if (v === 'ADMIN') return 'ADMIN';
  if (v === 'MEMBER') return 'MEMBER';
  return UNKNOWN_SUFFIX as SpaceRole;
}

function memberRoleFrom(dto: BackendUnknownRecord): SpaceRole {
  const v = asString(dto.role)?.toUpperCase();
  if (v === 'ADMIN') return 'ADMIN';
  if (v === 'MEMBER') return 'MEMBER';
  return UNKNOWN_SUFFIX as SpaceRole;
}

function workOrderStatusFrom(dto: BackendUnknownRecord): WorkOrderStatus {
  // 契约不对称：列表 VO 用 status，工单/通知详情 VO 用 work_order_status。详情字段名优先，列表兜底。
  const v = asString(dto.work_order_status ?? dto.status)?.toUpperCase();
  if (v === 'PENDING') return 'PENDING';
  if (v === 'APPROVED') return 'APPROVED';
  if (v === 'REJECTED') return 'REJECTED';
  return UNKNOWN_SUFFIX as WorkOrderStatus;
}

function workOrderItemTypeFrom(dto: BackendUnknownRecord): WorkOrderItemType {
  const v = asString(dto.item_type)?.toUpperCase();
  if (v === 'APPROVAL') return 'APPROVAL';
  // 后端 WorkOrderItemType 枚举为 APPROVAL/NOTICE；model 对外统一 NOTIFICATION，翻译 NOTICE→NOTIFICATION。
  if (v === 'NOTIFICATION' || v === 'NOTICE') return 'NOTIFICATION';
  return UNKNOWN_SUFFIX as WorkOrderItemType;
}

function joinStatusFrom(dto: BackendUnknownRecord): SpaceJoinStatus | undefined {
  const v = asString(dto.join_status)?.toUpperCase();
  if (v === 'JOINED') return 'JOINED';
  if (v === 'APPLYING') return 'APPLYING';
  if (v === 'NOT_JOINED') return 'NOT_JOINED';
  // 未给 join_status 时返回 undefined（卡片按 currentUserRole / isCreator 推断）
  return v ? (UNKNOWN_SUFFIX as SpaceJoinStatus) : undefined;
}

export function mapSpaceDto(dto: BackendUnknownRecord): { item: Space; warnings: string[] } {
  const warnings: string[] = [];
  const spaceType = spaceTypeFrom(dto);
  if (spaceType === UNKNOWN_SUFFIX) warnings.push('未知空间类型');
  const role = spaceRoleFrom(dto);
  const item: Space = {
    spaceId: asNumber(dto.space_id) ?? 0,
    spaceCode: asString(dto.space_code) ?? '',
    spaceName: asString(dto.space_name) ?? '未命名空间',
    spaceType,
    currentUserRole: asString(dto.current_user_role) ? role : undefined,
    isCreator: asBool(dto.is_creator),
    creatorUserId: asString(dto.creator_user_id),
    creatorUserName: asString(dto.creator_user_name),
    joinStatus: joinStatusFrom(dto),
    memberCount: asNumber(dto.member_count) ?? 0,
    ownerCount: asNumber(dto.owner_count) ?? 0,
    botCount: asNumber(dto.bot_count) ?? 0,
    gmtCreate: asString(dto.gmt_create),
    gmtModified: asString(dto.gmt_modified) ?? '',
  };
  return { item, warnings };
}

export function mapSpaceMemberDto(dto: BackendUnknownRecord): { item: SpaceMember; warnings: string[] } {
  const warnings: string[] = [];
  const role = memberRoleFrom(dto);
  if (role === UNKNOWN_SUFFIX) warnings.push('未知成员角色');
  const item: SpaceMember = {
    userId: asString(dto.user_id) ?? '',
    userName: asString(dto.user_name) ?? asString(dto.user_id) ?? '',
    displayName: asString(dto.display_name),
    role,
    botPermissionCount: asNumber(dto.bot_permission_count) ?? 0,
    isCreator: asBool(dto.is_creator) ?? false,
    gmtModified: asString(dto.gmt_modified) ?? '',
  };
  return { item, warnings };
}

export function mapWorkOrderDto(dto: BackendUnknownRecord): { item: WorkOrder; warnings: string[] } {
  const warnings: string[] = [];
  let itemType = workOrderItemTypeFrom(dto);
  const status = workOrderStatusFrom(dto);
  const eventType = asString(dto.event_type);

  // 详情 VO（GET work-orders/{id}）不带 item_type：按 event_type 推断分类，
  // 否则 itemType=UNKNOWN 会让 drawer 走进通知分支、审批 dl 不渲染。
  if (itemType === UNKNOWN_SUFFIX && eventType) {
    const meta = WORK_ORDER_TYPE_META[eventType];
    if (meta?.category === 'APPROVAL') itemType = 'APPROVAL';
    else if (meta?.category === 'NOTICE') itemType = 'NOTIFICATION';
  }
  if (itemType === UNKNOWN_SUFFIX) warnings.push('未知 item_type');
  if (status === UNKNOWN_SUFFIX) warnings.push('未知 status');

  // content 契约不对称：详情为结构化对象（见 synthesizeContentDisplay），列表/通知为字符串。
  // content = 列表/铃铛用的合成文案（保留）；contentRaw = 详情抽屉用的原始 JSON（仅对象 content 填充）。
  const rawContent = dto.content;
  const contentObj =
    typeof rawContent === 'object' && rawContent !== null && !Array.isArray(rawContent)
      ? (rawContent as BackendUnknownRecord)
      : undefined;
  const contentDisplay =
    typeof rawContent === 'string'
      ? asString(rawContent) ?? ''
      : contentObj
      ? synthesizeContentDisplay(contentObj)
      : '';
  const contentRaw = contentObj ? JSON.stringify(contentObj, null, 2) : undefined;
  // 列表/详情均有顶层 summary（与 content 同级，后端合成好的展示文案）：有则优先，替代 content.legacy_value。
  const summary = asString(dto.summary);
  // apply_reason / applicant_* 在详情里藏在 content 内，在列表里位于顶层；顶层优先、对象兜底。
  const applyReason = asString(dto.apply_reason) ?? (contentObj ? asString(contentObj.reason) : undefined);
  const applicantUserId =
    asString(dto.applicant_user_id) ?? (contentObj ? asString(contentObj.applicant_user_id) : undefined);
  const applicantName = asString(dto.applicant_name) ?? (contentObj ? asString(contentObj.applicant_name) : undefined);

  const typeMeta =
    itemType === 'APPROVAL'
      ? resolveWorkOrderType(eventType, 'APPROVAL')
      : noticeTypeMeta(asString(dto.notification_category));
  const item: WorkOrder = {
    itemId: asString(dto.item_id) ?? '',
    itemType,
    workOrderId: asNumber(dto.work_order_id) ?? 0,
    workOrderNo: asString(dto.work_order_no),
    notificationId: asNumber(dto.notification_id) ?? 0,
    notificationCategory: asString(dto.notification_category),
    bizType: asString(dto.biz_type) ?? '',
    // 契约更新后 biz_id 为字符串（如 "10001"），兼容历史数字
    bizId: asString(dto.biz_id) ?? String(asNumber(dto.biz_id) ?? ''),
    applicantUserId,
    applicantName,
    applyReason,
    reviewerUserId: asString(dto.reviewer_user_id),
    reviewerUserName: asString(dto.reviewer_user_name),
    reviewRemark: asString(dto.review_remark),
    reviewedAt: asString(dto.reviewed_at),
    recipientUserId: asString(dto.recipient_user_id),
    eventType: eventType ?? '',
    title: asString(dto.title) ?? '',
    content: summary ?? contentDisplay,
    contentRaw,
    status,
    statusLabel: WORK_ORDER_STATUS_LABEL[status],
    typeLabel: typeMeta.label,
    typeTone: typeMeta.tone,
    isRead: asBool(dto.is_read) ?? false,
    readAt: asString(dto.read_at),
    env: asString(dto.env),
    canApprove: asBool(dto.can_approve) ?? false,
    gmtCreated: asString(dto.gmt_created),
    gmtModified: asString(dto.gmt_modified) ?? '',
  };
  return { item, warnings };
}

/**
 * 通用分页映射：BackendApiPage<DTO> → { items: Domain[], total, page, pageSize, hasMore, warnings }。
 * 容错：真实后端可能返回 `list/page_no/page_size`（契约更新确认），故优先读通用字段，回退读 list/page_no/page_size。
 */
function mapList<Dto extends BackendUnknownRecord, Domain>(
  page: BackendApiPage<Dto> | undefined,
  mapOne: (dto: Dto) => { item: Domain; warnings: string[] },
): { items: Domain[]; total?: number; page: number; pageSize: number; hasMore?: boolean; warnings: string[] } {
  const raw = (page ?? {}) as BackendApiPage<Dto> & { list?: Dto[]; page_no?: number; page_size?: number };
  const rows = raw.items ?? raw.list ?? [];
  const results = rows.map(mapOne);
  return {
    items: results.map((r) => r.item),
    total: raw.total,
    page: raw.page ?? raw.page_no ?? 1,
    pageSize: raw.pageSize ?? raw.page_size ?? 20,
    hasMore: raw.hasMore,
    warnings: results.flatMap((r) => r.warnings),
  };
}

export function mapWorkOrderList(page?: BackendApiPage<BackendUnknownRecord>): WorkOrderListResult {
  return mapList(page, mapWorkOrderDto);
}

export function mapSpaceList(page?: BackendApiPage<BackendUnknownRecord>): SpaceListResult {
  return mapList(page, mapSpaceDto);
}

export function mapSpaceMemberList(page?: BackendApiPage<BackendUnknownRecord>): SpaceMemberListResult {
  return mapList(page, mapSpaceMemberDto);
}
