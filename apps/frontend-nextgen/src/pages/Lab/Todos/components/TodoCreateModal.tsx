import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Segmented } from '@/components/ui/Segmented';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type {
  TodoBotOption,
  TodoCreateSource,
  TodoSourceDraft,
  TodoSourceProject,
  TodoSourceType,
  TodoSourceWorkItem,
} from '@/domain/todos/models';
import { loadTodoSourceDraft, loadTodoSourceProjects, loadTodoSourceWorkItems } from '@/services/todos/todoService';
import { Loader2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

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
type SourceErrorKind = 'projects' | 'workItems' | 'draft';

export function TodoCreateModal({ open, bots, loading, onOpenChange, onSubmit }: TodoCreateModalProps) {
  const [sourceType, setSourceType] = useState<TodoSourceType>('manual');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [botId, setBotId] = useState('');
  const [projects, setProjects] = useState<TodoSourceProject[]>([]);
  const [projectId, setProjectId] = useState('');
  const [workItems, setWorkItems] = useState<TodoSourceWorkItem[]>([]);
  const [workItemId, setWorkItemId] = useState('');
  const [workItemQuery, setWorkItemQuery] = useState('');
  const [loadedDraft, setLoadedDraft] = useState<TodoSourceDraft | null>(null);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [workItemsLoading, setWorkItemsLoading] = useState(false);
  const [draftLoading, setDraftLoading] = useState(false);
  const [sourceError, setSourceError] = useState('');
  const [sourceErrorKind, setSourceErrorKind] = useState<SourceErrorKind | null>(null);
  const sourceRequestGeneration = useRef(0);

  useEffect(() => {
    sourceRequestGeneration.current += 1;
    setProjectsLoading(false);
    setWorkItemsLoading(false);
    setDraftLoading(false);
    if (!open) return;
    setSourceType('manual');
    setTitle('');
    setDescription('');
    setBotId(bots[0]?.botId || '');
    setProjects([]);
    setProjectId('');
    setWorkItems([]);
    setWorkItemId('');
    setWorkItemQuery('');
    setLoadedDraft(null);
    setSourceError('');
    setSourceErrorKind(null);
  }, [open]);

  useEffect(() => {
    if (!open || botId || bots.length === 0) return;
    setBotId(bots[0]?.botId || '');
  }, [botId, bots, open]);

  const fetchProjects = async () => {
    const generation = ++sourceRequestGeneration.current;
    setProjectsLoading(true);
    setSourceError('');
    setSourceErrorKind(null);
    try {
      const result = await loadTodoSourceProjects();
      if (generation !== sourceRequestGeneration.current) return;
      setProjects(result);
      setProjectId((current) => (result.some((item) => item.id === current) ? current : ''));
    } catch {
      if (generation !== sourceRequestGeneration.current) return;
      setProjects([]);
      setSourceError('Dima 项目加载失败，请重试。');
      setSourceErrorKind('projects');
    } finally {
      if (generation === sourceRequestGeneration.current) setProjectsLoading(false);
    }
  };

  const fetchWorkItems = async (selectedProjectId: string) => {
    const generation = ++sourceRequestGeneration.current;
    setWorkItemsLoading(true);
    setSourceError('');
    setSourceErrorKind(null);
    try {
      const result = await loadTodoSourceWorkItems(selectedProjectId);
      if (generation !== sourceRequestGeneration.current) return;
      setWorkItems(result);
    } catch {
      if (generation !== sourceRequestGeneration.current) return;
      setWorkItems([]);
      setSourceError('Dima 工作项加载失败，请重试。');
      setSourceErrorKind('workItems');
    } finally {
      if (generation === sourceRequestGeneration.current) setWorkItemsLoading(false);
    }
  };

  const changeSourceType = (next: TodoSourceType) => {
    sourceRequestGeneration.current += 1;
    setProjectsLoading(false);
    setWorkItemsLoading(false);
    setDraftLoading(false);
    setSourceType(next);
    setSourceError('');
    setSourceErrorKind(null);
    setProjectId('');
    setWorkItems([]);
    setWorkItemId('');
    setWorkItemQuery('');
    setLoadedDraft(null);
    setTitle('');
    setDescription('');
    if (next === 'dima') void fetchProjects();
  };

  const changeProject = (nextProjectId: string) => {
    sourceRequestGeneration.current += 1;
    setProjectId(nextProjectId);
    setWorkItemId('');
    setWorkItemQuery('');
    setWorkItems([]);
    setLoadedDraft(null);
    setTitle('');
    setDescription('');
    setDraftLoading(false);
    void fetchWorkItems(nextProjectId);
  };

  const changeWorkItem = async (nextWorkItemId: string) => {
    const generation = ++sourceRequestGeneration.current;
    setWorkItemId(nextWorkItemId);
    setLoadedDraft(null);
    setTitle('');
    setDescription('');
    setDraftLoading(true);
    setSourceError('');
    setSourceErrorKind(null);
    try {
      const draft = await loadTodoSourceDraft(projectId, nextWorkItemId);
      if (generation !== sourceRequestGeneration.current) return;
      setLoadedDraft(draft);
      setTitle(draft.title);
      setDescription(draft.description);
    } catch {
      if (generation !== sourceRequestGeneration.current) return;
      setLoadedDraft(null);
      setSourceError('Dima 工作项详情加载失败，请重试。');
      setSourceErrorKind('draft');
    } finally {
      if (generation === sourceRequestGeneration.current) setDraftLoading(false);
    }
  };

  const retrySourceRequest = () => {
    if (sourceErrorKind === 'draft' && projectId && workItemId) {
      void changeWorkItem(workItemId);
      return;
    }
    if (sourceErrorKind === 'workItems' && projectId) {
      void fetchWorkItems(projectId);
      return;
    }
    void fetchProjects();
  };

  const visibleWorkItems = useMemo(() => {
    const query = workItemQuery.trim().toLocaleLowerCase();
    if (!query) return workItems;
    return workItems.filter(
      (item) => item.title.toLocaleLowerCase().includes(query) || item.id.toLocaleLowerCase().includes(query),
    );
  }, [workItemQuery, workItems]);

  const selectedWorkItem = workItems.find((item) => item.id === workItemId);
  const submit = async () => {
    if (!title.trim() || !botId || draftLoading) return;
    if (sourceType === 'dima' && !loadedDraft) return;
    const success = await onSubmit({
      title: title.trim(),
      description: description.trim(),
      botId,
      ...(loadedDraft
        ? {
            source: {
              type: 'dima' as const,
              containerId: loadedDraft.projectId,
              itemId: loadedDraft.workItemId,
              ...(loadedDraft.sourceUrl ? { url: loadedDraft.sourceUrl } : {}),
              ...(loadedDraft.sourceUpdatedAt ? { updatedAt: loadedDraft.sourceUpdatedAt } : {}),
            },
          }
        : {}),
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
              value={sourceType}
              onChange={changeSourceType}
              options={[
                { value: 'manual', label: '手动创建' },
                { value: 'dima', label: 'Dima 导入' },
              ]}
              className="w-full"
            />
          </div>

          {sourceType === 'dima' ? (
            <div className="space-y-3 rounded-lg border border-border bg-muted/20 p-3">
              <label className="block space-y-1.5 text-xs font-medium text-foreground">
                <span className="flex items-center gap-1.5">
                  Dima 项目{' '}
                  {projectsLoading ? <Loader2 aria-label="Dima 项目加载中" className="size-3.5 animate-spin" /> : null}
                </span>
                <Select value={projectId || undefined} onValueChange={changeProject} disabled={projectsLoading}>
                  <SelectTrigger aria-label="Dima 项目">
                    <SelectValue placeholder={projectsLoading ? '项目加载中' : '请选择项目'} />
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

              {projectId ? (
                <>
                  <label className="block space-y-1.5 text-xs font-medium text-foreground">
                    <span>搜索 Dima 工作项</span>
                    <Input
                      aria-label="搜索 Dima 工作项"
                      value={workItemQuery}
                      onChange={(event) => setWorkItemQuery(event.target.value)}
                      placeholder="输入标题或工作项 ID"
                    />
                  </label>
                  <label className="block space-y-1.5 text-xs font-medium text-foreground">
                    <span className="flex items-center gap-1.5">
                      Req / Task / Bug
                      {workItemsLoading ? (
                        <Loader2 aria-label="Dima 工作项加载中" className="size-3.5 animate-spin" />
                      ) : null}
                    </span>
                    <Select
                      value={workItemId || undefined}
                      onValueChange={(value) => void changeWorkItem(value)}
                      disabled={workItemsLoading || workItems.length === 0}
                    >
                      <SelectTrigger aria-label="Dima 工作项">
                        <SelectValue placeholder={workItemsLoading ? '工作项加载中' : '请选择工作项'} />
                      </SelectTrigger>
                      <SelectContent>
                        {visibleWorkItems.map((item) => (
                          <SelectItem key={item.id} value={item.id}>
                            [{CATEGORY_LABELS[item.category]}] {item.title}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </label>
                </>
              ) : null}

              {selectedWorkItem ? (
                <p className="m-0 text-xs text-muted-foreground">
                  {CATEGORY_LABELS[selectedWorkItem.category]}
                  {selectedWorkItem.status ? ` · ${selectedWorkItem.status}` : ''}
                  {` · ${selectedWorkItem.id}`}
                </p>
              ) : null}
              {sourceError ? (
                <div className="flex items-center justify-between gap-3 text-xs text-destructive" role="alert">
                  <span>{sourceError}</span>
                  <Button size="sm" variant="outline" onClick={retrySourceRequest}>
                    重试
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}

          <label className="block space-y-1.5 text-xs font-medium text-foreground">
            <span className="flex items-center gap-1.5">
              标题 {draftLoading ? <Loader2 aria-label="Dima 详情加载中" className="size-3.5 animate-spin" /> : null}
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
        </div>
        <ModalFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => void submit()}
            loading={loading}
            disabled={!title.trim() || !botId || draftLoading || (sourceType === 'dima' && !loadedDraft)}
          >
            创建待办
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
