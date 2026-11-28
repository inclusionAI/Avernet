import { Button, ConfirmDialog, Empty, Spin } from '@/components/ui';
import { notifySuccess } from '@/components/ui/notify';
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import type { BotTeamRecord, SaveBotTeamInput, TodoBotOption } from '@/domain/todos/models';
import { addBotTeam, archiveTodoBotTeam, loadBotTeam, loadBotTeams, saveBotTeam } from '@/services/todos/todoService';
import { safeReportError } from '@/utils/errorToast';
import { Archive, ArrowLeft, Pencil, Plus, Workflow } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { BotTeamEditor } from './bot-team/BotTeamEditor';
import { CreateBotTeamModal } from './bot-team/CreateBotTeamModal';
import type { BotTeamEditorState } from './bot-team/botTeamEditorModel';
import { botTeamEditorFromDetail, formatBotTeamDateTime } from './bot-team/botTeamEditorModel';

interface BotTeamPageProps {
  spaceId?: number;
  bots: TodoBotOption[];
  onBack: () => void;
  onChanged?: () => void;
}

export function BotTeamPage({ spaceId, bots, onBack, onChanged }: BotTeamPageProps) {
  const [teams, setTeams] = useState<BotTeamRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editor, setEditor] = useState<BotTeamEditorState | null>(null);
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
      setEditor(botTeamEditorFromDetail(detail, mode));
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
        <Button
          type="button"
          variant="ghost"
          className="h-auto min-w-0 justify-start gap-2 p-0 text-left text-sm font-medium text-foreground hover:bg-transparent hover:text-primary"
          onClick={(event) => {
            event.stopPropagation();
            void openTeam(team, 'view');
          }}
        >
          <Workflow className="size-4 shrink-0" aria-hidden />
          <span className="truncate">{team.name}</span>
        </Button>
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
      cell: (team) => <span className="text-xs text-muted-foreground">{formatBotTeamDateTime(team.createdAt)}</span>,
    },
    {
      id: 'updatedAt',
      header: '更新时间',
      width: '12%',
      cell: (team) => <span className="text-xs text-muted-foreground">{formatBotTeamDateTime(team.updatedAt)}</span>,
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
          if (editor.nodes.some((node) => !node.name.trim() || !node.code.trim() || node.botIds.length === 0)) return;
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
            setEditor(botTeamEditorFromDetail(detail, 'view'));
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
