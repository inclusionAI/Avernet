/**
 * 「公开设置 → 协作能力」修复行（AC-23~30）：
 * 纯展示组件，修复 Hook 由父弹窗装配；按钮/分项结果/错误与刷新失败提示随 repair 状态更新。
 * 服务端已修复的项目保持原顺序展示，unknown 只表示结果未确认（不声称回滚）。
 */
import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { useBotCollaborationRepair } from '@/hooks/useBotCollaborationRepair';
import type { CollaborationRepairReport } from '@/services/bots/botCollaborationRepairService';

type ReportItem = CollaborationRepairReport['items'][number];
// 报告分项固定三项（backend 不再下发 caller_config）。
const componentText: Record<ReportItem['component'], string> = { ownership: '归属', profile: '基本资料', managers: '协作者' };
const statusText: Record<ReportItem['status'], string> = {
  succeeded: '已修复',
  failed: '修复失败',
  unknown: '结果未确认',
  skipped_dependency: '前置步骤未完成',
};

export function CollaborationRepairRow({
  repair,
  disabled,
  refreshFailed,
  disabledReason,
}: {
  repair: ReturnType<typeof useBotCollaborationRepair>;
  disabled: boolean;
  refreshFailed: boolean;
  /** 非 Owner / 编辑锁被他人持有时沿用公开设置既有禁用原因。 */
  disabledReason?: string;
}) {
  const action = (
    <Button variant="outline" size="sm" loading={repair.loading} disabled={disabled} onClick={() => void repair.run()}>
      {repair.loading ? '修复中…' : '修复信息'}
    </Button>
  );
  return (
    <div className="py-3" aria-label="修复 Bot 信息">
      {/* 标题/按钮始终相对整行内容垂直居中；分项与错误归入文本列，行展开后布局不偏移。 */}
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0 flex-1">
          <p className="m-0 text-sm font-medium text-foreground">修复 Bot 信息</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            修复当前 Bot 在「协作群」中的基本信息（名称、头像、归属、协作者等）
          </p>
          {repair.report && repair.report.status !== 'succeeded' && (
            <ul aria-live="polite" className="mt-2 text-xs text-muted-foreground">
              {repair.report.items.map((item) => (
                <li key={item.component}>
                  {componentText[item.component]}：{statusText[item.status]}
                </li>
              ))}
            </ul>
          )}
          {repair.error && (
            <p role="alert" className="mt-2 text-xs text-destructive">
              {repair.error}
            </p>
          )}
          {(repair.refreshError || (refreshFailed && repair.report && repair.report.status !== 'failed')) && (
            <p role="status" className="mt-2 text-xs text-muted-foreground">
              修复请求已完成，配置刷新失败，请重新打开查看
            </p>
          )}
        </div>
        {disabled && disabledReason ? (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex shrink-0" tabIndex={0}>
                  {action}
                </span>
              </TooltipTrigger>
              <TooltipContent>{disabledReason}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : (
          action
        )}
      </div>
    </div>
  );
}
