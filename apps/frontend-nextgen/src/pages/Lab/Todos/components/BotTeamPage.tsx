import { Badge, Button, ConfirmDialog, Empty, Input, Spin, Switch, Textarea } from '@/components/ui';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { notifySuccess } from '@/components/ui/notify';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import type { BotTeamDetail, BotTeamNode, BotTeamRecord, SaveBotTeamInput, TodoBotOption } from '@/domain/todos/models';
import {
  addBotTeam,
  archiveTodoBotTeam,
  loadBotTeam,
  loadBotTeams,
  saveBotTeam,
} from '@/services/todos/todoService';
import { cn } from '@/utils/cn';
import { safeReportError } from '@/utils/errorToast';
import {
  Archive,
  ArrowLeft,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  Pencil,
  Plus,
  Trash2,
  Workflow,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

interface BotTeamPageProps {
  spaceId?: number;
  bots: TodoBotOption[];
  onBack: () => void;
  onChanged?: () => void;
}

type EditorMode = 'view' | 'edit' | 'create';

interface EditorState {
  mode: EditorMode;
  botTeamId?: string;
  name: string;
  description: string;
  nodes: BotTeamNode[];
  createdBy?: string;
  updatedBy?: string;
  createdAt?: string;
  updatedAt?: string;
}

function nextNodeCode(nodes: BotTeamNode[]): string {
  const used = new Set(nodes.map((node) => node.code));
  let index = nodes.length + 1;
  while (used.has(`node_${index}`)) index += 1;
  return `node_${index}`;
}

function emptyNode(nodes: BotTeamNode[], botId = ''): BotTeamNode {
  const code = nextNodeCode(nodes);
  const displayIndex = nodes.length + 1;
  return {
    code,
    name: `节点 ${displayIndex}`,
    botId,
    approvalRequired: false,
    completionGate: { requiredArtifacts: [] },
  };
}

function latestRevision(detail: BotTeamDetail) {
  return (
    detail.revisions.find((revision) => revision.botTeamRevisionId === detail.botTeam.publishedRevisionId) ??
    [...detail.revisions].sort((left, right) => right.revisionNo - left.revisionNo)[0]
  );
}

function editorFromDetail(detail: BotTeamDetail, mode: 'view' | 'edit'): EditorState {
  const revision = latestRevision(detail);
  return {
    mode,
    botTeamId: detail.botTeam.botTeamId,
    name: revision?.name ?? detail.botTeam.name,
    description: revision?.description ?? detail.botTeam.description,
    nodes: revision?.nodes ?? [],
    createdBy: detail.botTeam.createdBy,
    updatedBy: detail.botTeam.updatedBy,
    createdAt: detail.botTeam.createdAt,
    updatedAt: detail.botTeam.updatedAt,
  };
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

export function BotTeamPage({ spaceId, bots, onBack, onChanged }: BotTeamPageProps) {
  const [teams, setTeams] = useState<BotTeamRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  const refresh = useCallback(async () => {
    if (!spaceId) {
      setTeams([]);
      return;
    }
    setLoading(true);
    try {
      const result = await loadBotTeams(spaceId, 'active');
      setTeams(result.items);
    } catch (cause) {
      setTeams([]);
      safeReportError(cause, { title: 'Bot 小队加载失败' });
    } finally {
      setLoading(false);
    }
  }, [spaceId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const openTeam = async (team: BotTeamRecord, mode: 'view' | 'edit') => {
    setDetailLoading(true);
    try {
      const detail = await loadBotTeam(team.botTeamId);
      setEditor(editorFromDetail(detail, mode));
    } catch (cause) {
      safeReportError(cause, { title: 'Bot 小队详情加载失败' });
    } finally {
      setDetailLoading(false);
    }
  };

  const archive = async (team: BotTeamRecord) => {
    try {
      await archiveTodoBotTeam(team.botTeamId);
      notifySuccess('Bot 小队已归档');
      await refresh();
      onChanged?.();
    } catch (cause) {
      safeReportError(cause, { title: 'Bot 小队归档失败' });
      throw cause;
    }
  };

  const columns: DataTableColumn<BotTeamRecord>[] = [
    {
      id: 'name',
      header: '名称',
      width: '18%',
      cell: (team) => (
        <button
          type="button"
          className="flex min-w-0 items-center gap-2 text-left text-sm font-medium text-foreground hover:text-primary"
          onClick={(event) => {
            event.stopPropagation();
            void openTeam(team, 'view');
          }}
        >
          <Workflow className="size-4 shrink-0" aria-hidden />
          <span className="truncate">{team.name}</span>
        </button>
      ),
    },
    {
      id: 'description',
      header: '描述',
      width: '27%',
      cell: (team) => <span className="block truncate text-xs text-muted-foreground">{team.description || '—'}</span>,
    },
    {
      id: 'type',
      header: '类型',
      width: '8%',
      cell: () => <span className="text-xs text-muted-foreground">工作流</span>,
    },
    {
      id: 'createdAt',
      header: '创建时间',
      width: '12%',
      cell: (team) => <span className="text-xs text-muted-foreground">{formatDateTime(team.createdAt)}</span>,
    },
    {
      id: 'updatedAt',
      header: '更新时间',
      width: '12%',
      cell: (team) => <span className="text-xs text-muted-foreground">{formatDateTime(team.updatedAt)}</span>,
    },
    {
      id: 'createdBy',
      header: '创建人',
      width: '8%',
      cell: (team) => <span className="block truncate text-xs text-muted-foreground">{team.createdBy}</span>,
    },
    {
      id: 'updatedBy',
      header: '更新人',
      width: '8%',
      cell: (team) => <span className="block truncate text-xs text-muted-foreground">{team.updatedBy}</span>,
    },
    {
      id: 'actions',
      header: '操作',
      width: '7%',
      align: 'end',
      headerAlign: 'end',
      cell: (team) => (
        <div className="flex justify-end gap-1" onClick={(event) => event.stopPropagation()}>
          <Button variant="ghost" size="icon" aria-label={`编辑 ${team.name}`} onClick={() => void openTeam(team, 'edit')}>
            <Pencil className="size-3.5" aria-hidden />
          </Button>
          <ConfirmDialog
            title="归档 Bot 小队"
            description={`归档「${team.name}」后不能再用于新待办，已有待办不受影响。`}
            confirmText="归档"
            confirmVariant="destructive"
            onConfirm={() => archive(team)}
          >
            <Button variant="ghost" size="icon" aria-label={`归档 ${team.name}`}>
              <Archive className="size-3.5" aria-hidden />
            </Button>
          </ConfirmDialog>
        </div>
      ),
    },
  ];

  if (editor) {
    return (
      <BotTeamEditor
        editor={editor}
        bots={bots}
        loading={saving}
        onChange={setEditor}
        onBack={() => setEditor(null)}
        onEdit={() => setEditor((current) => (current ? { ...current, mode: 'edit' } : current))}
        onSave={async () => {
          if (!spaceId || !editor.name.trim() || editor.nodes.length === 0) return;
          if (editor.nodes.some((node) => !node.name.trim() || !node.code.trim() || !node.botId)) return;
          const input: SaveBotTeamInput = {
            name: editor.name.trim(),
            description: editor.description.trim(),
            nodes: editor.nodes,
          };
          setSaving(true);
          try {
            const detail = editor.botTeamId
              ? await saveBotTeam(editor.botTeamId, input)
              : await addBotTeam({ ...input, spaceId });
            notifySuccess(editor.botTeamId ? 'Bot 小队已更新并发布新版本' : 'Bot 小队已创建');
            setEditor(editorFromDetail(detail, 'view'));
            await refresh();
            onChanged?.();
          } catch (cause) {
            safeReportError(cause, { title: editor.botTeamId ? 'Bot 小队更新失败' : 'Bot 小队创建失败' });
          } finally {
            setSaving(false);
          }
        }}
      />
    );
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="icon" aria-label="返回待办" onClick={onBack}>
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <h2 className="m-0 text-xl font-semibold leading-[30px] text-foreground">Bot 小队</h2>
        <span className="text-xs text-muted-foreground">· 无分支工作流，逐节点执行 + 平台 Bot 门禁校验</span>
      </div>
      <div className="flex items-center justify-end">
        <Button
          leftIcon={<Plus className="size-3.5" aria-hidden />}
          disabled={!spaceId || bots.length === 0}
          onClick={() => setCreateOpen(true)}
        >
          创建小队
        </Button>
      </div>

      <CreateBotTeamModal
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreate={({ name, description }) => {
          setCreateOpen(false);
          setEditor({ mode: 'create', name, description, nodes: [] });
        }}
      />

      {detailLoading || loading ? (
        <Spin tip={detailLoading ? '加载小队详情中…' : '加载 Bot 小队中…'} className="py-16" />
      ) : teams.length === 0 ? (
        <Empty title="暂无 Bot 小队" description="创建一条线性工作流，把不同 Bot 按节点串联起来。" />
      ) : (
        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <DataTable
            ariaLabel="Bot 小队列表"
            columns={columns}
            rows={teams}
            getRowKey={(team) => team.botTeamId}
            onRowClick={(team) => void openTeam(team, 'view')}
            minWidth="min-w-[1120px]"
          />
        </div>
      )}
    </section>
  );
}

function CreateBotTeamModal({
  open,
  onOpenChange,
  onCreate,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (input: { name: string; description: string }) => void;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName('');
    setDescription('');
    setSubmitted(false);
  }, [open]);

  const create = () => {
    setSubmitted(true);
    if (!name.trim()) return;
    onCreate({ name: name.trim(), description: description.trim() });
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="md" aria-describedby={undefined}>
        <ModalHeader>
          <ModalTitle className="text-base">创建小队</ModalTitle>
          <ModalDescription>先填写基本信息，创建后继续配置线性工作流。</ModalDescription>
        </ModalHeader>
        <div className="space-y-4">
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>名称</span>
            <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="如：数据分析小队" />
            {submitted && !name.trim() ? <span className="block text-xs text-destructive">请输入小队名称</span> : null}
          </label>
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>描述</span>
            <Textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="小队擅长场景与节点能力概要"
              rows={3}
            />
          </label>
          <fieldset className="space-y-1.5">
            <legend className="text-sm font-medium text-foreground">类型</legend>
            <div className="grid grid-cols-2 gap-2">
              <div className="flex flex-col items-start gap-1 rounded-lg border border-primary bg-primary/5 p-3 text-left">
                <Workflow className="size-5 text-primary" aria-hidden />
                <span className="text-sm font-medium text-foreground">工作流</span>
              </div>
            </div>
          </fieldset>
        </div>
        <ModalFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={create}>创建</Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

function BotTeamEditor({
  editor,
  bots,
  loading,
  onChange,
  onBack,
  onEdit,
  onSave,
}: {
  editor: EditorState;
  bots: TodoBotOption[];
  loading: boolean;
  onChange: (next: EditorState) => void;
  onBack: () => void;
  onEdit: () => void;
  onSave: () => Promise<void>;
}) {
  const editable = editor.mode !== 'view';
  const [selectedNodeIndex, setSelectedNodeIndex] = useState(0);
  const selectedNode = editor.nodes[selectedNodeIndex];

  useEffect(() => {
    setSelectedNodeIndex((current) => Math.max(0, Math.min(current, editor.nodes.length - 1)));
  }, [editor.nodes.length]);

  const updateNode = (index: number, update: Partial<BotTeamNode>) => {
    onChange({
      ...editor,
      nodes: editor.nodes.map((node, nodeIndex) => (nodeIndex === index ? { ...node, ...update } : node)),
    });
  };

  const addNodeAfter = (index: number) => {
    const nodes = [...editor.nodes];
    const insertAt = Math.min(index + 1, nodes.length);
    nodes.splice(insertAt, 0, emptyNode(nodes, bots[0]?.botId));
    onChange({ ...editor, nodes });
    setSelectedNodeIndex(insertAt);
  };

  const removeNode = (index: number) => {
    onChange({ ...editor, nodes: editor.nodes.filter((_, itemIndex) => itemIndex !== index) });
    setSelectedNodeIndex((current) => (current > index ? current - 1 : Math.min(current, editor.nodes.length - 2)));
  };

  const moveNode = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= editor.nodes.length) return;
    const nodes = [...editor.nodes];
    [nodes[index], nodes[target]] = [nodes[target], nodes[index]];
    onChange({ ...editor, nodes });
    setSelectedNodeIndex(target);
  };

  const valid = Boolean(
    editor.name.trim() &&
      editor.nodes.length > 0 &&
      editor.nodes.every((node) => node.name.trim() && node.code.trim() && node.botId),
  );
  const selectedBot = selectedNode ? bots.find((bot) => bot.botId === selectedNode.botId) : undefined;

  return (
    <section className="space-y-5">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" aria-label="返回 Bot 小队列表" onClick={onBack}>
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <div className="min-w-0">
          <h2 className="m-0 truncate text-lg font-semibold text-foreground">{editor.name || '创建 Bot 小队'}</h2>
          <p className="m-0 mt-0.5 text-xs text-muted-foreground">按节点从左到右线性执行</p>
        </div>
        {editor.mode === 'view' ? (
          <Button className="ml-auto" onClick={onEdit} leftIcon={<Pencil className="size-3.5" aria-hidden />}>
            编辑
          </Button>
        ) : (
          <Button className="ml-auto" loading={loading} disabled={!valid} onClick={() => void onSave()}>
            {editor.mode === 'create' ? '创建并发布' : '保存并发布新版本'}
          </Button>
        )}
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <h3 className="m-0 mb-3 text-sm font-semibold text-foreground">基本信息</h3>
        {editable ? (
          <div className="grid gap-4 md:grid-cols-2">
            <label className="space-y-1.5 text-xs font-medium text-foreground">
              <span>名称</span>
              <Input
                value={editor.name}
                onChange={(event) => onChange({ ...editor, name: event.target.value })}
                placeholder="如：Coding 小队"
              />
            </label>
            <label className="space-y-1.5 text-xs font-medium text-foreground md:col-span-2">
              <span>描述</span>
              <Textarea
                value={editor.description}
                onChange={(event) => onChange({ ...editor, description: event.target.value })}
                placeholder="小队擅长场景与节点能力概要"
                rows={3}
              />
            </label>
          </div>
        ) : (
          <div className="space-y-2">
            <dl className="m-0 grid grid-cols-[72px_minmax(0,1fr)] gap-x-4 gap-y-2 text-xs md:grid-cols-[72px_minmax(0,1fr)_72px_minmax(0,2fr)]">
              <dt className="text-muted-foreground">名称:</dt>
              <dd className="m-0 truncate font-medium text-foreground">{editor.name}</dd>
              <dt className="text-muted-foreground">描述:</dt>
              <dd className="m-0 truncate text-foreground/85">{editor.description || '—'}</dd>
            </dl>
            <dl className="m-0 grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2 lg:grid-cols-5">
              <div>
                <dt className="inline text-muted-foreground">类型:</dt>{' '}
                <dd className="m-0 inline text-foreground/85">工作流</dd>
              </div>
              <div>
                <dt className="inline text-muted-foreground">创建时间:</dt>{' '}
                <dd className="m-0 inline text-foreground/85">{editor.createdAt ? formatDateTime(editor.createdAt) : '—'}</dd>
              </div>
              <div>
                <dt className="inline text-muted-foreground">更新时间:</dt>{' '}
                <dd className="m-0 inline text-foreground/85">{editor.updatedAt ? formatDateTime(editor.updatedAt) : '—'}</dd>
              </div>
              <div>
                <dt className="inline text-muted-foreground">创建人:</dt>{' '}
                <dd className="m-0 inline text-foreground/85">{editor.createdBy || '—'}</dd>
              </div>
              <div>
                <dt className="inline text-muted-foreground">更新人:</dt>{' '}
                <dd className="m-0 inline text-foreground/85">{editor.updatedBy || '—'}</dd>
              </div>
            </dl>
          </div>
        )}
      </div>

      <div className="rounded-xl border border-border bg-card p-5">
        <div className="mb-4">
          <h3 className="m-0 text-sm font-semibold text-foreground">工作流</h3>
          <p className="m-0 mt-1 text-xs text-muted-foreground">点选节点后，在下方配置执行 Bot、审批和完成门禁。</p>
        </div>

        <div className="app-scrollbar overflow-x-auto rounded-xl border border-dashed border-border bg-muted/10 px-5 py-7">
          <ol className="m-0 flex min-w-max list-none items-center p-0">
            {editor.nodes.map((node, index) => {
              const bot = bots.find((item) => item.botId === node.botId);
              const selected = index === selectedNodeIndex;
              return (
                <li key={`${node.code}-${index}`} className="flex items-center">
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setSelectedNodeIndex(index)}
                      className={cn(
                        'w-44 rounded-xl border bg-card px-4 py-4 text-left shadow-sm transition-colors',
                        selected ? 'border-primary ring-2 ring-primary/10' : 'border-border hover:border-primary/50',
                      )}
                    >
                      <span className="mb-3 flex items-center gap-2">
                        <span
                          className={cn(
                            'flex size-7 items-center justify-center rounded-full text-xs font-semibold',
                            selected ? 'bg-primary text-primary-foreground' : 'bg-primary/10 text-primary',
                          )}
                        >
                          {index + 1}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
                          {node.name || `节点 ${index + 1}`}
                        </span>
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">{bot?.botName || node.botId || '未选择 Bot'}</span>
                      <span className="mt-3 flex flex-wrap gap-1.5">
                        {node.approvalRequired ? (
                          <Badge className="border-0 bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning">需审批</Badge>
                        ) : null}
                        {node.completionGate.requiredArtifacts.length ? (
                          <Badge className="border-0 bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                            {node.completionGate.requiredArtifacts.length} 项门禁
                          </Badge>
                        ) : null}
                      </span>
                    </button>
                    {editable ? (
                      <button
                        type="button"
                        aria-label={`删除 ${node.name}`}
                        onClick={() => removeNode(index)}
                        className="absolute -right-2 -top-2 flex size-5 items-center justify-center rounded-full border border-border bg-card text-muted-foreground hover:text-destructive"
                      >
                        <Trash2 className="size-3" aria-hidden />
                      </button>
                    ) : null}
                  </div>

                  {index < editor.nodes.length - 1 || editable ? (
                    <div className="relative flex h-8 w-16 items-center justify-center">
                      <span className="absolute left-0 right-1 top-1/2 h-px bg-border" aria-hidden />
                      {index < editor.nodes.length - 1 ? (
                        <ArrowRight className="absolute right-0 size-4 -translate-y-px text-border" aria-hidden />
                      ) : null}
                      {editable ? (
                        <button
                          type="button"
                          aria-label={`在 ${node.name || `节点 ${index + 1}`} 后添加节点`}
                          onClick={() => addNodeAfter(index)}
                          className="relative z-10 flex size-7 items-center justify-center rounded-full border border-border bg-card text-muted-foreground hover:bg-accent hover:text-primary"
                        >
                          <Plus className="size-4" aria-hidden />
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                </li>
              );
            })}
            {editor.nodes.length === 0 && editable ? (
              <li>
                <Button
                  variant="outline"
                  className="border-dashed"
                  leftIcon={<Plus className="size-3.5" aria-hidden />}
                  onClick={() => {
                    const node = emptyNode([], bots[0]?.botId);
                    onChange({ ...editor, nodes: [node] });
                    setSelectedNodeIndex(0);
                  }}
                >
                  添加首个节点
                </Button>
              </li>
            ) : null}
            {editor.nodes.length === 0 && !editable ? (
              <li className="py-5 text-xs text-muted-foreground">该小队尚未配置节点</li>
            ) : null}
          </ol>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-card p-5">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <h3 className="m-0 text-sm font-semibold text-foreground">节点配置</h3>
          {selectedNode ? <span className="text-xs text-muted-foreground">· {selectedNode.name}</span> : null}
          {editable && selectedNode ? (
            <div className="ml-auto flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                aria-label="节点前移"
                disabled={selectedNodeIndex === 0}
                onClick={() => moveNode(selectedNodeIndex, -1)}
              >
                <ChevronLeft className="size-4" aria-hidden />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="节点后移"
                disabled={selectedNodeIndex === editor.nodes.length - 1}
                onClick={() => moveNode(selectedNodeIndex, 1)}
              >
                <ChevronRight className="size-4" aria-hidden />
              </Button>
            </div>
          ) : null}
        </div>

        {selectedNode ? (
          <div className="space-y-5">
            <div className="grid gap-4 md:grid-cols-2">
              <label className="space-y-1.5 text-xs font-medium text-foreground">
                <span>节点名称</span>
                <Input
                  value={selectedNode.name}
                  disabled={!editable}
                  onChange={(event) => updateNode(selectedNodeIndex, { name: event.target.value })}
                />
              </label>
              <label className="space-y-1.5 text-xs font-medium text-foreground">
                <span>节点 code</span>
                <Input
                  value={selectedNode.code}
                  disabled={!editable}
                  className="font-mono"
                  onChange={(event) => updateNode(selectedNodeIndex, { code: event.target.value })}
                />
              </label>
              <label className="space-y-1.5 text-xs font-medium text-foreground">
                <span>执行 Bot</span>
                <Select
                  value={selectedNode.botId || undefined}
                  disabled={!editable || bots.length === 0}
                  onValueChange={(botId) => updateNode(selectedNodeIndex, { botId })}
                >
                  <SelectTrigger aria-label={`节点 ${selectedNodeIndex + 1} 执行 Bot`}>
                    <SelectValue placeholder="选择 Bot" />
                  </SelectTrigger>
                  <SelectContent>
                    {bots.map((bot) => (
                      <SelectItem key={bot.botId} value={bot.botId}>
                        {bot.botName}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {!editable ? <span className="block text-[11px] text-muted-foreground">{selectedBot?.botName}</span> : null}
              </label>
              <div className="space-y-1.5 text-xs font-medium text-foreground">
                <span className="block">是否审批</span>
                <div className="flex h-9 items-center gap-2 rounded-md border border-input bg-background px-3">
                  <Switch
                    size="sm"
                    checked={selectedNode.approvalRequired}
                    disabled={!editable}
                    onCheckedChange={(approvalRequired) => updateNode(selectedNodeIndex, { approvalRequired })}
                  />
                  <span>{selectedNode.approvalRequired ? '需人工审批' : '无需审批'}</span>
                </div>
              </div>
            </div>

            <div className="border-t border-divider pt-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <p className="m-0 text-xs font-semibold text-foreground">完成门禁</p>
                  <p className="m-0 mt-0.5 text-[11px] text-muted-foreground">按产物类型、状态和最少数量判断节点是否完成。</p>
                </div>
                {editable ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    leftIcon={<Plus className="size-3.5" aria-hidden />}
                    onClick={() =>
                      updateNode(selectedNodeIndex, {
                        completionGate: {
                          requiredArtifacts: [
                            ...selectedNode.completionGate.requiredArtifacts,
                            { artifactType: 'text', artifactState: 'created', minCount: 1 },
                          ],
                        },
                      })
                    }
                  >
                    添加门禁
                  </Button>
                ) : null}
              </div>

              {selectedNode.completionGate.requiredArtifacts.length === 0 ? (
                <p className="m-0 rounded-lg border border-dashed border-border px-3 py-3 text-xs text-muted-foreground">
                  未配置产物门禁
                </p>
              ) : (
                <div className="space-y-2">
                  {selectedNode.completionGate.requiredArtifacts.map((gate, gateIndex) => (
                    <div key={`${gate.artifactType}-${gateIndex}`} className="grid gap-2 md:grid-cols-[1fr_1fr_120px_32px]">
                      <Input
                        aria-label="产物类型"
                        value={gate.artifactType}
                        disabled={!editable}
                        placeholder="产物类型"
                        onChange={(event) => {
                          const requiredArtifacts = selectedNode.completionGate.requiredArtifacts.map((item, itemIndex) =>
                            itemIndex === gateIndex ? { ...item, artifactType: event.target.value } : item,
                          );
                          updateNode(selectedNodeIndex, { completionGate: { requiredArtifacts } });
                        }}
                      />
                      <Input
                        aria-label="产物状态"
                        value={gate.artifactState}
                        disabled={!editable}
                        placeholder="产物状态"
                        onChange={(event) => {
                          const requiredArtifacts = selectedNode.completionGate.requiredArtifacts.map((item, itemIndex) =>
                            itemIndex === gateIndex ? { ...item, artifactState: event.target.value } : item,
                          );
                          updateNode(selectedNodeIndex, { completionGate: { requiredArtifacts } });
                        }}
                      />
                      <Input
                        aria-label="最少数量"
                        type="number"
                        min={1}
                        value={gate.minCount}
                        disabled={!editable}
                        onChange={(event) => {
                          const minCount = Math.max(1, Number(event.target.value) || 1);
                          const requiredArtifacts = selectedNode.completionGate.requiredArtifacts.map((item, itemIndex) =>
                            itemIndex === gateIndex ? { ...item, minCount } : item,
                          );
                          updateNode(selectedNodeIndex, { completionGate: { requiredArtifacts } });
                        }}
                      />
                      {editable ? (
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="删除门禁"
                          onClick={() =>
                            updateNode(selectedNodeIndex, {
                              completionGate: {
                                requiredArtifacts: selectedNode.completionGate.requiredArtifacts.filter(
                                  (_, itemIndex) => itemIndex !== gateIndex,
                                ),
                              },
                            })
                          }
                        >
                          <Trash2 className="size-3.5" aria-hidden />
                        </Button>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="py-8 text-center text-sm text-muted-foreground">暂无可配置节点</div>
        )}
      </div>
    </section>
  );
}
