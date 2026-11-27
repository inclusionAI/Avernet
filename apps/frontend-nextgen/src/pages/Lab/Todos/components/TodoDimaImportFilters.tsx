import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type { TodoExecutor, TodoExecutorOption, TodoOwner, TodoSourceProject } from '@/domain/todos/models';
import { Search } from 'lucide-react';
import { TodoExecutorSelect } from './TodoExecutorSelect';

interface TodoDimaImportFiltersProps {
  projects: TodoSourceProject[];
  projectId: string;
  projectsLoading: boolean;
  query: string;
  executorOptions: TodoExecutorOption[];
  executor?: TodoExecutor;
  owners: TodoOwner[];
  ownerId: string;
  onProjectChange: (projectId: string) => void;
  onQueryChange: (query: string) => void;
  onExecutorChange: (executor: TodoExecutor) => void;
  onOwnerChange: (ownerId: string) => void;
}

export function TodoDimaImportFilters({
  projects,
  projectId,
  projectsLoading,
  query,
  executorOptions,
  executor,
  owners,
  ownerId,
  onProjectChange,
  onQueryChange,
  onExecutorChange,
  onOwnerChange,
}: TodoDimaImportFiltersProps) {
  return (
    <div className="grid gap-3 rounded-xl border border-border bg-muted/15 p-4 md:grid-cols-2 xl:grid-cols-[200px_minmax(180px,1fr)_220px_180px]">
      <label className="space-y-1.5 text-xs font-medium text-foreground">
        <span className="block">Dima 空间 / 项目</span>
        <Select value={projectId || undefined} onValueChange={onProjectChange}>
          <SelectTrigger aria-label="选择 Dima 空间或项目">
            <SelectValue placeholder={projectsLoading ? '加载中…' : '请选择'} />
          </SelectTrigger>
          <SelectContent>
            {projects.map((project) => (
              <SelectItem key={project.id} value={project.id}>
                {project.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>
      <label className="space-y-1.5 text-xs font-medium text-foreground">
        <span className="block">搜索</span>
        <span className="relative block">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="标题 / 工作项 ID"
            aria-label="搜索 Dima 工作项"
            className="pl-9"
          />
        </span>
      </label>
      <label className="space-y-1.5 text-xs font-medium text-foreground">
        <span className="block">本批次执行方</span>
        <TodoExecutorSelect options={executorOptions} value={executor} onChange={onExecutorChange} />
      </label>
      <label className="space-y-1.5 text-xs font-medium text-foreground">
        <span className="block">本批次负责人</span>
        <Select value={ownerId || undefined} onValueChange={onOwnerChange} disabled={owners.length === 0}>
          <SelectTrigger aria-label="选择导入待办负责人">
            <SelectValue placeholder="请选择负责人" />
          </SelectTrigger>
          <SelectContent>
            {owners.map((owner) => (
              <SelectItem key={owner.userId} value={owner.userId}>
                {owner.userName}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>
    </div>
  );
}
