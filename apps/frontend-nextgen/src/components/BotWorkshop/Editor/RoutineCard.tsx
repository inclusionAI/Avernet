// 定时任务卡片:从 RoutinePanel 拆出的单卡视图(artboard-075/079)。
// 三字段一行等宽、Prompt 双行截断、底部操作药丸;启停等待态由 pendingRoutineToggle 注入。
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Switch } from '@/components/ui/Switch';
import type { BotEditorRoutine } from '@/domain/botEditor';
import type { PendingRoutineToggle } from '@/hooks/useRoutineActivation';
import { getRoutineScheduleLabel } from '@/services/botWorkshop/routineSchedule';
import { Loader2 } from 'lucide-react';

// artboard-035:卡片底部操作药丸 —— 白底、10px 圆角、浅投影、13px 中灰文字。
const pillButtonClassName = 'h-7 rounded-[10px] px-3 text-[13px] text-zinc-600 shadow-[0_1px_2px_rgba(0,0,0,0.03)]';

function formatRoutineDate(value?: string) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

export function RoutineCard({
  item,
  editable,
  pendingRoutineToggle,
  onToggle,
  onDelete,
  onEdit,
}: {
  item: BotEditorRoutine;
  editable: boolean;
  /** 启停中的任务等待态：开关进入 loading，成功后原位更新，不整页刷新列表。 */
  pendingRoutineToggle?: PendingRoutineToggle;
  onToggle: (routine: BotEditorRoutine) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onEdit: (item: BotEditorRoutine) => void;
}) {
  return (
    <section className="rounded-2xl bg-page-canvas p-4 transition-shadow hover:shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="m-0 truncate text-sm leading-6 font-medium">{item.name}</h3>
          <p className="m-0 mt-1.5 text-xs text-muted-foreground">更新时间：{formatRoutineDate(item.modifiedAt)}</p>
        </div>
        <div className="flex items-center gap-2">
          {pendingRoutineToggle?.id === item.id ? (
            <span role="status" className="inline-flex items-center gap-1 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
              {pendingRoutineToggle.slow
                ? `${pendingRoutineToggle.enabled ? '启用' : '停用'}耗时较长，仍在等待结果…`
                : `正在${pendingRoutineToggle.enabled ? '启用' : '停用'}…`}
            </span>
          ) : null}
          <Switch
            checked={item.enabled}
            loading={Boolean(pendingRoutineToggle)}
            disabled={!editable}
            aria-label={`${item.enabled ? '停用' : '启用'}${item.name}`}
            onCheckedChange={() => void onToggle(item)}
          />
        </div>
      </div>
      {/* artboard-075/079:三字段一行等宽分布（justify-between 同时命中窄卡 8px 间距与宽卡 ~111px 分散），下次执行必现、缺值以 — 占位。 */}
      <dl className="mt-5 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <dt className="text-xs leading-[18px] text-zinc-400">执行频率</dt>
          <dd className="m-0 mt-0.5 text-[13px] leading-5">{getRoutineScheduleLabel(item.cron)}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs leading-[18px] text-zinc-400">下次执行</dt>
          <dd className="m-0 mt-0.5 text-[13px] leading-5">{formatRoutineDate(item.nextRunAt)}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs leading-[18px] text-zinc-400">执行配置</dt>
          <dd className="m-0 mt-0.5 truncate text-[13px] leading-5">模型：{item.model || '跟随 Bot 默认模型'}</dd>
        </div>
      </dl>
      <dl className="mt-4">
        <dt className="text-xs leading-[18px] text-zinc-400">Prompt</dt>
        <dd className="m-0 mt-0.5 line-clamp-2 break-words text-[13px] leading-5">{item.command}</dd>
      </dl>
      {/* artboard-075/079:卡内内容与底部操作药丸之间有 #EEEEEE 分割线（宽随内容区）。 */}
      <div className="mt-4 flex justify-end gap-2 border-t border-divider/55 pt-3">
        <Button
          variant="outline"
          size="sm"
          className={pillButtonClassName}
          disabled={!editable}
          aria-label={`编辑${item.name}`} onClick={() => onEdit(item)}
        >
          编辑
        </Button>
        <ConfirmDialog
          title="删除定时任务"
          description={`确认删除「${item.name}」？删除后无法恢复。`}
          confirmVariant="destructive"
          onConfirm={() => onDelete(item.id)}
          disabled={!editable}
        >
          <Button
            variant="outline"
            size="sm"
            className={`${pillButtonClassName} text-destructive hover:bg-destructive/10 hover:text-destructive`}
            aria-label={`删除${item.name}`}
          >
            删除
          </Button>
        </ConfirmDialog>
      </div>
    </section>
  );
}
