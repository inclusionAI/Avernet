import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useWorkflowCloneSource } from '@/hooks/taskEscortParity/useWorkflowCloneSource';
import { useId, useState } from 'react';

interface Props {
  context: TaskEscortParityContext;
  sourceId: string;
  disabled: boolean;
  purpose?: 'clone' | 'ai';
  onSelect: (sourceId: string, title: string) => void;
}
export function WorkflowCloneSource({ context, sourceId, disabled, onSelect, purpose = 'clone' }: Props) {
  const source = useWorkflowCloneSource(context, true, sourceId);
  const searchId = useId();
  const [search, setSearch] = useState('');
  const query = search.trim().toLowerCase();
  const candidates = (source.list.data ?? []).filter(
    (row) => row.title.toLowerCase().includes(query) || row.workflowId.toLowerCase().includes(query),
  );
  const selected = source.list.data?.find((row) => row.workflowId === sourceId);
  return (
    <section aria-label={purpose === 'ai' ? 'AI 来源工作流' : '基于已有工作流创建'} className="space-y-3">
      <div className="space-y-1.5">
        <label htmlFor={searchId} className="text-xs font-medium">
          {purpose === 'ai' ? '搜索 AI 上下文工作流' : '搜索源工作流'}
        </label>
        <Input
          id={searchId}
          type="search"
          value={search}
          disabled={disabled}
          placeholder="按名称或 ID 搜索"
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      <section aria-label="候选源工作流" className="max-h-44 overflow-y-auto rounded-md border border-input bg-card">
        {source.list.loading ? (
          <p role="status" className="p-3 text-xs text-muted-foreground">
            正在读取候选工作流…
          </p>
        ) : source.list.error ? (
          <div className="p-3 text-xs">
            <p role="alert" className="text-destructive">
              候选工作流读取失败：{source.list.error}
            </p>
            <Button size="sm" variant="link" onClick={source.list.refresh}>
              重试读取候选
            </Button>
          </div>
        ) : candidates.length === 0 ? (
          <p className="p-3 text-xs text-muted-foreground">没有匹配的候选工作流。</p>
        ) : (
          <ul className="divide-y divide-border">
            {candidates.map((row) => (
              <li key={row.workflowId}>
                <Button
                  variant="ghost"
                  className={`h-auto w-full justify-start rounded-none px-3 py-2 text-left ${
                    row.workflowId === sourceId ? 'bg-primary/10 text-primary' : ''
                  }`}
                  disabled={disabled}
                  aria-pressed={row.workflowId === sourceId}
                  onClick={() => onSelect(row.workflowId, row.title)}
                >
                  <span className="min-w-0">
                    <span className="block truncate">{row.title}</span>
                    <span className="block truncate font-mono text-xs text-muted-foreground">{row.workflowId}</span>
                  </span>
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>
      {selected && (
        <section aria-label="源工作流只读预览" className="space-y-2 rounded-md border border-input bg-card p-3 text-xs">
          <h3 className="font-semibold">源工作流只读预览</h3>
          <p className="break-all font-mono">{selected.workflowId}</p>
          {source.preview.loading ? (
            <p role="status" className="text-muted-foreground">
              正在读取快照…
            </p>
          ) : source.preview.error ? (
            <>
              <p role="alert" className="text-destructive">
                快照读取失败：{source.preview.error}
              </p>
              <Button size="sm" variant="link" onClick={source.preview.refresh}>
                重试读取快照
              </Button>
            </>
          ) : (
            source.preview.data && (
              <dl className="space-y-1 break-all">
                <div>
                  <dt className="text-muted-foreground">Git Pack</dt>
                  <dd>{source.preview.data.sourcePackId}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">源 Git commit</dt>
                  <dd className="font-mono">{source.preview.data.baseCommit}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">脚本数量</dt>
                  <dd>{source.preview.data.scriptCount}</dd>
                </div>
              </dl>
            )
          )}
        </section>
      )}
      <p className="text-xs leading-5 text-muted-foreground">
        {purpose === 'ai'
          ? 'AI 生成需要先选择一个有编辑权限的已有工作流作为上下文。完整 YAML 和需求将发送到生成服务；不会修改或自动发布来源工作流。'
          : '复制工作流 YAML 与 Pack 脚本到独立 Pack（默认使用新工作流 ID）。源工作流不会被修改，不复制运行历史或部署状态。'}
      </p>
    </section>
  );
}
