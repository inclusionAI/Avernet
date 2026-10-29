// 公开风险动线提示条（bot-public-risk-check）——从 PublicationEditor 拆出的纯展示片段：
// 检测/审批在途卡、失败告警条与流程预告条；派生规则留在编辑器，此处只按传入态渲染。
import type { BotPublicRiskCheckRecord } from '@/services/collaborationPrivacy/botPublicRiskService';
import { Check, Lock, ShieldCheck, TriangleAlert } from 'lucide-react';

// 公开流程两步胶囊:「检测 → 审批」本身是有序步骤,用 step 条编码当前所处环节。
const stepTone = 'flex items-center gap-1 rounded-full px-2.5 py-1 text-xs';
const stepActive = `${stepTone} bg-primary/10 font-medium text-primary`;
const stepPending = `${stepTone} bg-muted/70 text-muted-foreground`;

/** 在途终态卡:进行中提示 + 两步进度 + 工单入口,及「审批前可见性不变」的锁定说明。 */
export function RiskInFlightBanner({
  kind,
  record,
  currentScopeNote,
}: {
  kind: 'checking' | 'advancing' | 'approving';
  record: BotPublicRiskCheckRecord | null;
  currentScopeNote: string;
}) {
  return (
    <>
      <div className="rounded-lg border border-primary/25 bg-primary/[0.04] px-4 py-3.5">
        <p className="m-0 text-sm font-medium text-foreground">
          {kind === 'approving' ? '公开工单审批进行中' : 'Bot公开风险前置检测进行中'}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          {kind === 'approving'
            ? '公开风险检测已通过，工单审批结果可通过消息中心或工单中心查看。'
            : '预计需要2～3分钟完成；检测通过后将自动为你发起公开工单审批，可稍后回到本页或消息中心查看结果。'}
        </p>
        <ol className="mt-3 flex flex-wrap items-center gap-1.5" aria-label="公开流程进度">
          <li className={stepActive}>
            {kind === 'approving' && <Check className="size-3" aria-hidden />}
            风险前置检测
          </li>
          <li aria-hidden className="px-0.5 text-xs text-muted-foreground">
            →
          </li>
          <li className={kind === 'approving' ? stepActive : stepPending}>公开工单审批</li>
        </ol>
        <div className="mt-3 border-t border-primary/15 pt-2.5 text-xs">
          {record?.detail_url ? (
            <a href={record.detail_url} target="_blank" rel="noopener noreferrer" className="font-medium text-primary hover:opacity-80">
              查看检测工单
            </a>
          ) : record ? (
            <span className="text-muted-foreground">检测工单号：{record.risk_check_id}</span>
          ) : null}
        </div>
      </div>
      <p className="m-0 flex items-center gap-1.5 text-xs text-muted-foreground">
        <Lock className="size-3.5 shrink-0" aria-hidden />
        审批通过前，当前可见性保持不变：{currentScopeNote}
      </p>
    </>
  );
}

/** 终局失败/需关注告警条:提示未提交并引导去检测工单看明细。 */
export function RiskFailureBanner({
  kind,
  record,
}: {
  kind: 'failed' | 'attention';
  record: BotPublicRiskCheckRecord | null;
}) {
  return (
    <div className="flex gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3">
      <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
      <div>
        <p className="m-0 text-sm font-medium text-foreground">
          Bot公开风险前置检测失败{kind === 'attention' ? '（检测服务异常）' : ''}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          本次公开变更未提交。
          {record?.detail_url ? (
            <>
              请前往
              <a href={record.detail_url} target="_blank" rel="noopener noreferrer" className="font-medium text-primary hover:opacity-80">
                检测工单
              </a>
              查看风险明细与处理建议，处理后重新发起公开。
            </>
          ) : (
            '请前往检测工单查看风险明细与处理建议，处理后重新发起公开。'
          )}
        </p>
      </div>
    </div>
  );
}

/** 未发起时的流程预告条:让用户在提交前知道「检测 → 审批」两步动线。 */
export function RiskFlowPreviewBanner() {
  return (
    <div className="flex gap-3 rounded-lg border border-warning/30 bg-warning/5 px-4 py-3">
      <ShieldCheck className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
      <div>
        <p className="m-0 text-sm font-medium text-foreground">发起后进入公开审核流程</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Bot公开风险前置检测 → 公开工单审批；审批通过前当前可见性保持不变。
        </p>
      </div>
    </div>
  );
}
