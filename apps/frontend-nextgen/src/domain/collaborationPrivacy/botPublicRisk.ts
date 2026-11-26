// Bot 公开风险前置检测——纯逻辑（bot-public-risk-check change，design D1/D4）。
// deriveRiskUiState 与老前端（open-claw riskFlowState）同构；phase 为后端开放字符串，
// 未知值按保守锁定兜底（行编辑禁用 + 通用文案），绝不因未知值解锁。

import type {
  BotPublicRiskCheckRecord,
  CreateBotPublicRiskRequest,
} from '@/services/backendApi/collaboration/collaborationBotPublicRiskController';
import type { ViewDept } from '@/services/backendApi/collaboration/collaborationPublicationController';
import { publicConfigsEqual } from './policies';
import type { PublicConfig, PublicScope } from './types';

/** 记录最小结构（结构化兼容 BotPublicRiskCheckRecord，独立可测）。 */
export type BotPublicRiskRecordLike = Pick<
  BotPublicRiskCheckRecord,
  'risk_check_id' | 'phase' | 'security_status' | 'detail_url'
> &
  Partial<BotPublicRiskCheckRecord>;

/** 通用配置 / 编辑弹窗的派生展示态。 */
export type BotPublicRiskUiState =
  | { kind: 'idle' }
  | { kind: 'checking'; record: BotPublicRiskRecordLike }
  | { kind: 'advancing'; record: BotPublicRiskRecordLike }
  | { kind: 'approving'; record: BotPublicRiskRecordLike | null }
  | { kind: 'failed'; record: BotPublicRiskRecordLike }
  | { kind: 'attention'; record: BotPublicRiskRecordLike };

/**
 * 派生展示态（先到先得）：
 * 1. 无记录 / CLOSED、PUBLISHED → idle（完成后按当前配置正常展示）
 * 2. NEEDS_ATTENTION → attention（流程需处理，恢复可编辑）
 * 3. REJECT → failed（检测未通过，恢复可编辑）
 * 4. APPROVING → approving（行「审批中」+ 工单中心进度入口）
 * 5. PASS / ADVANCING → advancing（交接中过渡）
 * 6. PREPARING / CHECKING / 未知 phase → checking（保守锁定）
 */
export function deriveRiskUiState(record: BotPublicRiskRecordLike | null): BotPublicRiskUiState {
  if (!record || record.phase === 'CLOSED' || record.phase === 'PUBLISHED') {
    return { kind: 'idle' };
  }
  if (record.phase === 'NEEDS_ATTENTION') return { kind: 'attention', record };
  if (record.security_status === 'REJECT') return { kind: 'failed', record };
  if (record.phase === 'APPROVING') return { kind: 'approving', record };
  if (record.security_status === 'PASS' || record.phase === 'ADVANCING') return { kind: 'advancing', record };
  return { kind: 'checking', record };
}

/** 重试沿用失败申请的对用户公开目标及组织范围。 */
export function getRiskRetryIntent(
  state: BotPublicRiskUiState,
): Pick<CreateBotPublicRiskRequest, 'visibility' | 'view_depts'> | null {
  if (state.kind !== 'failed' && state.kind !== 'attention') return null;
  const target = state.record.target;
  const visibility = target?.visibility.toLowerCase();
  if (target?.public_scope !== 'user' || (visibility !== 'public' && visibility !== 'protected')) return null;
  return { visibility, view_depts: target.view_depts };
}

/** 失败标题、恢复操作和行级简短状态使用同一映射。 */
export function getRiskFailurePresentation(
  kind: 'failed' | 'attention',
  record: BotPublicRiskRecordLike | null,
) {
  const approved = record?.approval_decision === 'AGREE';
  switch (kind === 'attention' ? record?.error_reason : null) {
    case 'CONFIG_CHANGED':
      return {
        label: '配置已变更',
        rowSummary: {
          title: '本次公开未生效',
          description: 'Bot 配置已变更，请按当前配置重新申请。',
        },
        title: approved ? '配置已变更，本次公开未生效' : '配置已变更，请重新申请',
        description: approved
          ? '审批已通过，但期间 Bot 配置（如 MCP 或其他资源）发生变更。为保障安全，本次公开已被拦截，请按当前配置重新申请。'
          : 'Bot 配置已偏离送扫快照，本次申请未生效，请按当前配置重新申请。',
        canRefresh: false,
      };
    case 'PERMISSION_CHANGED':
      return {
        label: '权限已变更',
        title: '权限已变更，本次公开未生效',
        description: `${approved ? '审批已通过，但' : ''}原申请人管理权限或 Bot 归属已变化，请确认权限后重新申请。`,
        canRefresh: false,
      };
    case 'INPUT_OR_PERMISSION_INVALID':
      return {
        label: '配置或权限已变更',
        title: '配置或权限已变更，请重新申请',
        description: '申请期间 Bot 配置或申请人权限发生变化，本次公开未生效。',
        canRefresh: false,
      };
    default:
      return kind === 'failed'
        ? {
            label: '检测未通过',
            title: 'Bot 公开风险前置检测未通过',
            description: '请前往检测工单查看风险明细并整改，整改后可重新申请。',
            canRefresh: true,
          }
        : {
            label: '流程异常',
            title: '公开流程状态异常',
            description: '请先刷新状态确认，或重新发起公开申请。',
            canRefresh: true,
          };
  }
}

/** 发布提交通道：direct=现行直改（收紧/外部 Bot）、risk-check=前置检测动线、none=未变化。 */
export type BotPublicPublishChannel = 'direct' | 'risk-check' | 'none';

/**
 * 分流门（design D1）：目标收紧（none）或外部 Bot → direct；目标公开向且有差异 → risk-check；
 * 差异判定复用 publicConfigsEqual（scope + 组织路径），不做语义等效压缩。
 */
export function resolvePublishChannel(
  target: PublicConfig,
  initial: PublicConfig,
  isExternalBot: boolean,
): BotPublicPublishChannel {
  // 顺序：先 none（未变化，编辑器本就禁用）；外部 Bot 有差异直改；收紧直改；其余走前置检测。
  if (publicConfigsEqual(target, initial)) return 'none';
  if (isExternalBot) return 'direct';
  if (target.scope === 'none') return 'direct';
  return 'risk-check';
}

/**
 * 目标 scope → OpenAPI 申请视图（visibility + view_depts）。
 * restricted 时 view_depts 按后端约束 deptNo/deptName 均非空：deptNo 缺失（选择器尚未回填部门编码的
 * 历史配置）时以路径拼接名兜底为 deptNo——后端仅要求非空字符串，展示名相同不损失语义。
 */
export function toBotPublicRiskVisibility(
  scope: PublicScope,
  deptEntries?: Array<{ deptNo: string; deptName: string }>,
): { visibility: 'public' | 'protected'; view_depts: ViewDept[] } {
  if (scope === 'restricted') {
    const viewDepts = (deptEntries ?? []).map((entry) => {
      const name = entry.deptName || entry.deptNo || '';
      return { deptNo: entry.deptNo || name, deptName: name };
    });
    return { visibility: 'protected', view_depts: viewDepts };
  }
  return { visibility: 'public', view_depts: [] };
}
