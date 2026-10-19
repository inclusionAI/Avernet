// @asset-migrated: teamclaw 自研
/** WorkflowSubPanel —— 能力菜单「工作流任务」行的 hover 子面板。
 *
 * dmore 实测（hd_f54eba11 index.html，2026-10-10）：面板 240×236 rx12（无标题行），
 * 搜索框 206×28 rx8（占位 12px #B9BEC5）+ 行 218×34 rx12（icon 14px + 13px 文案）。
 * 列表懒加载自 useWorkflowList（clawweb GET /api/workflows），搜索框做前端即时过滤。
 */
import { Button, Input } from '@/components/ui';
import { Search, Workflow } from 'lucide-react';
import { useMemo, useState } from 'react';

export interface WorkflowPanelItem {
  workflowId: string;
  title: string;
}

/** 工作流子面板列表：搜索框 + 34px rx12 行 + 空态；前端即时过滤。 */
export function WorkflowSubPanel({
  workflows,
  loading,
  onPick,
}: {
  workflows: WorkflowPanelItem[];
  loading: boolean;
  onPick: (w: WorkflowPanelItem) => void;
}) {
  const [q, setQ] = useState('');
  const keyword = q.trim().toLowerCase();
  const filtered = useMemo(
    () =>
      keyword
        ? workflows.filter(
            (w) => w.title.toLowerCase().includes(keyword) || w.workflowId.toLowerCase().includes(keyword),
          )
        : workflows,
    [workflows, keyword],
  );
  return (
    <div className="flex flex-col gap-1">
      <div className="relative">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute left-2.5 top-1/2 size-3 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索工作流"
          className="h-[28px] rounded-lg pl-8 text-xs"
        />
      </div>
      {loading && <p className="px-3 py-2 text-xs text-muted-foreground">加载中…</p>}
      {!loading && filtered.length === 0 && (
        <p className="px-3 py-2 text-xs text-muted-foreground">
          {keyword ? '无匹配工作流' : '未加载到工作流，请确认 Bot 可用工作流'}
        </p>
      )}
      <div className="flex max-h-[170px] flex-col gap-1 overflow-y-auto">
        {filtered.map((w) => (
          <Button
            key={w.workflowId}
            variant="ghost"
            onClick={() => onPick(w)}
            className="h-[34px] w-full justify-start gap-1.5 rounded-xl px-2.5 text-left"
          >
            <Workflow aria-hidden="true" className="size-3.5 shrink-0" />
            <span className="min-w-0 flex-1 truncate text-[13px] font-normal text-foreground">{w.title}</span>
          </Button>
        ))}
      </div>
    </div>
  );
}
