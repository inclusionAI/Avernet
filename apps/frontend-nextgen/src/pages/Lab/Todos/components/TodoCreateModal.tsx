import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Segmented } from '@/components/ui/Segmented';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type { TodoBotOption, TodoCreateSource } from '@/domain/todos/models';
import { Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTodoSourceImport } from './useTodoSourceImport';

interface TodoFormValues {
  title: string;
  description: string;
  botId: string;
  source?: TodoCreateSource;
}

interface TodoCreateModalProps {
  open: boolean;
  bots: TodoBotOption[];
  loading: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (input: TodoFormValues) => Promise<boolean>;
}

const CATEGORY_LABELS = { req: '需求', task: '任务', bug: '缺陷' } as const;

export function TodoCreateModal({ open, bots, loading, onOpenChange, onSubmit }: TodoCreateModalProps) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [botId, setBotId] = useState('');
  const source = useTodoSourceImport({ open, setTitle, setDescription });

  useEffect(() => {
    if (!open) return;
    setTitle('');
    setDescription('');
    setBotId(bots[0]?.botId || '');
  }, [open]);

  useEffect(() => {
    if (!open || botId || bots.length === 0) return;
    setBotId(bots[0]?.botId || '');
  }, [botId, bots, open]);

  const submit = async () => {
    if (!title.trim() || !botId || source.draftLoading) return;
    if (source.sourceType === 'dima' && !source.loadedDraft) return;
    const success = await onSubmit({
      title: title.trim(),
      description: description.trim(),
      botId,
      ...(source.createSource ? { source: source.createSource } : {}),
    });
    if (success) onOpenChange(false);
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="lg" aria-describedby={undefined}>
        <ModalHeader>
          <ModalTitle>创建待办</ModalTitle>
          <ModalDescription>可手动创建，也可从当前账号有权访问的 Dima 工作项导入后编辑。</ModalDescription>
        </ModalHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <span className="block text-xs font-medium text-foreground">来源</span>
            <Segmented
              aria-label="待办来源"
              value={source.sourceType}
              onChange={source.changeSourceType}
              options={[
                { value: 'manual', label: '手动创建' },
                { value: 'dima', label: 'Dima 导入' },
              ]}
              className="w-full"
            />
          </div>

          {source.sourceType === 'dima' ? (
            <div className="space-y-3 rounded-lg border border-border bg-muted/20 p-3">
              <label className="block space-y-1.5 text-xs font-medium text-foreground">
                <span className="flex items-center gap-1.5">
                  Dima 项目{' '}
                  {source.projectsLoading ? (
                    <Loader2 aria-label="Dima 项目加载中" className="size-3.5 animate-spin" />
                  ) : null}
                </span>
                <Select
                  value={source.projectId || undefined}
                  onValueChange={source.changeProject}
                  disabled={source.projectsLoading}
                >
                  <SelectTrigger aria-label="Dima 项目">
                    <SelectValue placeholder={source.projectsLoading ? '项目加载中' : '请选择项目'} />
                  </SelectTrigger>
                  <SelectContent>
                    {source.projects.map((project) => (
                      <SelectItem key={project.id} value={project.id}>
                        {project.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>

              {source.projectId ? (
                <>
                  <label className="block space-y-1.5 text-xs font-medium text-foreground">
                    <span>搜索 Dima 工作项</span>
                    <Input
                      aria-label="搜索 Dima 工作项"
                      value={source.workItemQuery}
                      onChange={(event) => source.setWorkItemQuery(event.target.value)}
                      placeholder="输入标题或工作项 ID"
                    />
                  </label>
                  <label className="block space-y-1.5 text-xs font-medium text-foreground">
                    <span className="flex items-center gap-1.5">
                      Req / Task / Bug
                      {source.workItemsLoading ? (
                        <Loader2 aria-label="Dima 工作项加载中" className="size-3.5 animate-spin" />
                      ) : null}
                    </span>
                    <Select
                      value={source.workItemId || undefined}
                      onValueChange={(value) => void source.changeWorkItem(value)}
                      disabled={source.workItemsLoading || source.workItems.length === 0}
                    >
                      <SelectTrigger aria-label="Dima 工作项">
                        <SelectValue placeholder={source.workItemsLoading ? '工作项加载中' : '请选择工作项'} />
                      </SelectTrigger>
                      <SelectContent>
                        {source.visibleWorkItems.map((item) => (
                          <SelectItem key={item.id} value={item.id}>
                            [{CATEGORY_LABELS[item.category]}] {item.title}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </label>
                </>
              ) : null}

              {source.selectedWorkItem ? (
                <p className="m-0 text-xs text-muted-foreground">
                  {CATEGORY_LABELS[source.selectedWorkItem.category]}
                  {source.selectedWorkItem.status ? ` · ${source.selectedWorkItem.status}` : ''}
                  {` · ${source.selectedWorkItem.id}`}
                </p>
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

          <label className="block space-y-1.5 text-xs font-medium text-foreground">
            <span className="flex items-center gap-1.5">
              标题{' '}
              {source.draftLoading ? <Loader2 aria-label="Dima 详情加载中" className="size-3.5 animate-spin" /> : null}
            </span>
            <Input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="例如：分析本周用户反馈"
              autoFocus={source.sourceType === 'manual'}
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
        </div>
        <ModalFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => void submit()}
            loading={loading}
            disabled={
              !title.trim() ||
              !botId ||
              source.draftLoading ||
              (source.sourceType === 'dima' && !source.loadedDraft)
            }
          >
            创建待办
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
