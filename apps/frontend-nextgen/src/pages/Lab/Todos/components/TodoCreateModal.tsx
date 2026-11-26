import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Segmented } from '@/components/ui/Segmented';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type { TodoBotOption, TodoCreateSource, TodoSourceType } from '@/domain/todos/models';
import { cn } from '@/utils/cn';
import { Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { TodoAutoImportForm } from './TodoAutoImportForm';
import { useTodoSourceImport } from './useTodoSourceImport';

interface TodoFormValues {
  title: string;
  description: string;
  botId: string;
  source?: TodoCreateSource;
}

interface TodoCreateModalProps {
  open: boolean;
  sourceType: TodoSourceType;
  spaceId?: number;
  bots: TodoBotOption[];
  loading: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (input: TodoFormValues) => Promise<boolean>;
}

const CATEGORY_LABELS = { req: '需求', task: '任务', bug: '缺陷' } as const;
type DimaImportMode = 'manual' | 'automatic';

export function TodoCreateModal({
  open,
  sourceType,
  spaceId,
  bots,
  loading,
  onOpenChange,
  onSubmit,
}: TodoCreateModalProps) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [botId, setBotId] = useState('');
  const [dimaImportMode, setDimaImportMode] = useState<DimaImportMode>('manual');
  const source = useTodoSourceImport({ open, sourceType, setTitle, setDescription });

  useEffect(() => {
    if (!open) return;
    setTitle('');
    setDescription('');
    setBotId(bots[0]?.botId || '');
    setDimaImportMode('manual');
  }, [open]);

  useEffect(() => {
    if (!open || botId || bots.length === 0) return;
    setBotId(bots[0]?.botId || '');
  }, [botId, bots, open]);

  const submit = async () => {
    if (!title.trim() || !botId || source.draftLoading) return;
    if (sourceType === 'dima' && !source.loadedDraft) return;
    const success = await onSubmit({
      title: title.trim(),
      description: description.trim(),
      botId,
      ...(source.createSource ? { source: source.createSource } : {}),
    });
    if (success) onOpenChange(false);
  };

  const workspaces = source.projects.filter((item) => item.kind === 'workspace');
  const dimaProjects = source.projects.filter((item) => item.kind === 'project');
  const selectedContainer = source.projects.find((item) => item.id === source.projectId);

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="lg" aria-describedby={undefined}>
        <ModalHeader>
          <ModalTitle>{sourceType === 'dima' ? '导入 Dima 待办' : '创建待办'}</ModalTitle>
          <ModalDescription>
            {sourceType === 'dima'
              ? '从当前登录账号有权访问的 Dima 工作项导入，并选择负责执行的 Bot。'
              : '填写待办信息，并选择负责执行的 Bot。'}
          </ModalDescription>
        </ModalHeader>
        <div className="space-y-4 outline-none">
          {sourceType === 'dima' ? (
            <Segmented
              aria-label="Dima 导入方式"
              value={dimaImportMode}
              onChange={setDimaImportMode}
              options={[
                { value: 'manual', label: '手动导入' },
                { value: 'automatic', label: '自动导入' },
              ]}
              className="w-full"
            />
          ) : null}

          {sourceType === 'dima' && dimaImportMode === 'manual' ? (
            <div className="space-y-3 rounded-lg border border-border bg-muted/20 p-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="m-0 text-sm font-medium text-foreground">选择 Dima 来源</p>
                  <p className="m-0 mt-0.5 text-xs text-muted-foreground">
                    {source.projectsLoading
                      ? '正在读取当前登录账号可访问的空间和项目…'
                      : `当前账号可访问 ${workspaces.length} 个空间、${dimaProjects.length} 个项目`}
                  </p>
                </div>
                {source.projectsLoading ? (
                  <Loader2 aria-label="Dima 空间加载中" className="mt-0.5 size-4 shrink-0 animate-spin" />
                ) : null}
              </div>

              {!source.projectsLoading && source.projects.length === 0 && !source.sourceError ? (
                <p className="m-0 rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                  当前账号没有可访问的 Dima 空间或项目。
                </p>
              ) : null}

              {source.projects.length > 0 ? (
                <div className="space-y-1.5">
                  <span className="block text-xs font-medium text-foreground">空间 / 项目</span>
                  <div
                    className="grid max-h-36 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-2"
                    role="list"
                    aria-label="Dima 空间和项目"
                  >
                    {source.projects.map((project) => {
                      const selected = source.projectId === project.id;
                      const kindLabel = project.kind === 'workspace' ? '空间' : '项目';
                      return (
                        <Button
                          key={project.id}
                          variant="ghost"
                          aria-label={`选择 Dima ${kindLabel} ${project.name}`}
                          aria-pressed={selected}
                          onClick={() => source.changeProject(project.id)}
                          className={cn(
                            'h-auto min-w-0 justify-start rounded-md border px-2.5 py-2 text-left',
                            selected
                              ? 'border-primary bg-primary/10 text-foreground hover:bg-primary/10'
                              : 'border-border bg-background text-foreground hover:bg-accent',
                          )}
                        >
                          <span className="min-w-0">
                            <span className="block truncate text-xs font-medium">{project.name}</span>
                            <span className="mt-0.5 block text-[11px] font-normal text-muted-foreground">
                              {kindLabel} · {project.id}
                            </span>
                          </span>
                        </Button>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              {source.projectId ? (
                <div className="space-y-2 border-t border-border pt-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs font-medium text-foreground">
                      {selectedContainer?.kind === 'workspace' ? '空间' : '项目'}内工作项
                    </span>
                    {source.workItemsLoading ? (
                      <Loader2 aria-label="Dima 工作项加载中" className="size-3.5 animate-spin" />
                    ) : (
                      <span className="text-[11px] text-muted-foreground">共 {source.workItems.length} 条</span>
                    )}
                  </div>
                  <Input
                    aria-label="搜索 Dima 工作项"
                    value={source.workItemQuery}
                    onChange={(event) => source.setWorkItemQuery(event.target.value)}
                    placeholder="输入标题或工作项 ID"
                  />
                  {!source.workItemsLoading && source.workItems.length === 0 && !source.sourceError ? (
                    <p className="m-0 rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                      当前{selectedContainer?.kind === 'workspace' ? '空间' : '项目'}暂无可导入的 Req / Task / Bug。
                    </p>
                  ) : null}
                  {!source.workItemsLoading && source.workItems.length > 0 && source.visibleWorkItems.length === 0 ? (
                    <p className="m-0 rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                      没有匹配的 Dima 工作项。
                    </p>
                  ) : null}
                  {source.visibleWorkItems.length > 0 ? (
                    <div className="max-h-44 space-y-1.5 overflow-y-auto pr-1" role="list" aria-label="Dima 工作项">
                      {source.visibleWorkItems.map((item) => {
                        const selected = source.workItemId === item.id;
                        return (
                          <Button
                            key={item.id}
                            variant="ghost"
                            aria-label={`导入 Dima 工作项 ${item.title}`}
                            aria-pressed={selected}
                            onClick={() => void source.changeWorkItem(item.id)}
                            className={cn(
                              'h-auto w-full justify-between rounded-md border px-2.5 py-2 text-left',
                              selected
                                ? 'border-primary bg-primary/10 text-foreground hover:bg-primary/10'
                                : 'border-border bg-background text-foreground hover:bg-accent',
                            )}
                          >
                            <span className="min-w-0">
                              <span className="block truncate text-xs font-medium">
                                [{CATEGORY_LABELS[item.category]}] {item.title}
                              </span>
                              <span className="mt-0.5 block text-[11px] font-normal text-muted-foreground">
                                {item.status ? `${item.status} · ` : ''}
                                {item.id}
                              </span>
                            </span>
                            {selected && source.draftLoading ? (
                              <Loader2 aria-label="Dima 详情加载中" className="size-3.5 shrink-0 animate-spin" />
                            ) : null}
                          </Button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              ) : null}

              {source.sourceError ? (
                <div className="flex items-center justify-between gap-3 text-xs text-destructive" role="alert">
                  <span>{source.sourceError}</span>
                  <Button size="sm" variant="outline" onClick={source.retrySourceRequest}>
                    重试
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}

          {sourceType === 'dima' && dimaImportMode === 'automatic' ? (
            <TodoAutoImportForm
              active={open}
              spaceId={spaceId}
              projects={source.projects}
              projectsLoading={source.projectsLoading}
              projectsError={source.sourceError}
              bots={bots}
              botId={botId}
              onBotIdChange={setBotId}
              onRetryProjects={source.retrySourceRequest}
            />
          ) : null}

          {sourceType === 'manual' || dimaImportMode === 'manual' ? (
            <>
              <label className="block space-y-1.5 text-xs font-medium text-foreground">
                <span className="flex items-center gap-1.5">
                  标题{' '}
                  {source.draftLoading ? (
                    <Loader2 aria-label="Dima 详情加载中" className="size-3.5 animate-spin" />
                  ) : null}
                </span>
                <Input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="例如：分析本周用户反馈"
                  autoFocus={sourceType === 'manual'}
                />
              </label>
              <label className="block space-y-1.5 text-xs font-medium text-foreground">
                <span>描述</span>
                <Textarea
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="补充 Bot 需要完成的目标和要求"
                  rows={5}
                />
              </label>
              <label className="block space-y-1.5 text-xs font-medium text-foreground">
                <span>选择 Bot</span>
                <Select value={botId || undefined} onValueChange={setBotId} disabled={bots.length === 0}>
                  <SelectTrigger aria-label="选择 Bot">
                    <SelectValue placeholder={bots.length ? '请选择 Bot' : '当前空间暂无可用 Bot'} />
                  </SelectTrigger>
                  <SelectContent>
                    {bots.map((bot) => (
                      <SelectItem key={bot.botId} value={bot.botId}>
                        {bot.botName || bot.botId}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
              {bots.length === 0 ? (
                <p className="m-0 text-xs text-muted-foreground">当前空间没有可用 Bot，暂时无法创建待办。</p>
              ) : null}
            </>
          ) : null}
        </div>
        {sourceType === 'manual' || dimaImportMode === 'manual' ? (
          <ModalFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button
              onClick={() => void submit()}
              loading={loading}
              disabled={
                !title.trim() || !botId || source.draftLoading || (sourceType === 'dima' && !source.loadedDraft)
              }
            >
              {sourceType === 'dima' ? '导入待办' : '创建待办'}
            </Button>
          </ModalFooter>
        ) : null}
      </ModalContent>
    </Modal>
  );
}
