import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
import { Switch } from '@/components/ui/Switch';
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import { notifySuccess } from '@/components/ui/notify';
import type { TodoAutoImportRule, TodoBotOption, TodoSourceProject } from '@/domain/todos/models';
import { loadTodoSourceProjects } from '@/services/todos/todoService';
import { safeReportError } from '@/utils/errorToast';
import { ArrowLeft, Pencil, Plus } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { TodoAutomationEditor } from './TodoAutomationEditor';
import { useTodoAutoImportRules } from './useTodoAutoImportRules';

interface TodoAutomationPageProps {
  spaceId?: number;
  bots: TodoBotOption[];
  schedulerBots: TodoBotOption[];
  onBack: () => void;
}

function formatDateTime(value?: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function projectNames(rule: TodoAutoImportRule, projects: TodoSourceProject[]): string {
  const resolvedNames = rule.sourceContainerIds
    .map((id) => projects.find((project) => project.id === id)?.name)
    .filter(Boolean);
  return resolvedNames.length ? resolvedNames.join('、') : rule.sourceContainerIds.join('、');
}

export function TodoAutomationPage({ spaceId, bots, schedulerBots, onBack }: TodoAutomationPageProps) {
  const rules = useTodoAutoImportRules({ active: true, spaceId });
  const [projects, setProjects] = useState<TodoSourceProject[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [editing, setEditing] = useState<TodoAutoImportRule | 'create' | null>(null);

  useEffect(() => {
    let aborted = false;
    setProjectsLoading(true);
    void loadTodoSourceProjects()
      .then((items) => {
        if (!aborted) setProjects(items);
      })
      .catch((cause) => {
        if (aborted) return;
        setProjects([]);
        safeReportError(cause, { title: 'Dima 项目加载失败' });
      })
      .finally(() => {
        if (!aborted) setProjectsLoading(false);
      });
    return () => {
      aborted = true;
    };
  }, []);

  const columns = useMemo<DataTableColumn<TodoAutoImportRule>[]>(
    () => [
      {
        id: 'projects',
        header: 'Dima 空间 / 项目',
        width: '24%',
        cell: (rule) => <span className="block truncate text-sm font-medium text-foreground">{projectNames(rule, projects)}</span>,
      },
      {
        id: 'filter',
        header: '导入条件',
        width: '30%',
        cell: (rule) => <span className="block line-clamp-2 text-xs text-muted-foreground">{rule.filterText}</span>,
      },
      {
        id: 'bot',
        header: '待办执行 Bot',
        width: '16%',
        cell: (rule) => (
          <span className="block truncate text-xs text-muted-foreground">
            {bots.find((bot) => bot.botId === rule.botId)?.botName || rule.botId}
          </span>
        ),
      },
      {
        id: 'schedulerBot',
        header: '定时任务执行者',
        width: '16%',
        cell: (rule) => (
          <span className="block truncate text-xs text-muted-foreground">
            {schedulerBots.find((bot) => bot.botId === rule.schedulerBotId)?.botName ||
              rule.schedulerBotId ||
              '未配置'}
          </span>
        ),
      },
      {
        id: 'last',
        header: '最近同步',
        width: '14%',
        cell: (rule) => <span className="text-xs text-muted-foreground">{formatDateTime(rule.cursorUpdatedAt)}</span>,
      },
      {
        id: 'enabled',
        header: '启动',
        width: '12%',
        cell: (rule) => (
          <Switch
            size="sm"
            checked={rule.status === 'enabled'}
            loading={rules.updatingRuleId === rule.ruleId}
            onCheckedChange={(checked) => void rules.setRuleEnabled(rule, checked)}
            aria-label={`${rule.status === 'enabled' ? '停用' : '启用'} ${projectNames(rule, projects)}`}
          />
        ),
      },
      {
        id: 'actions',
        header: '操作',
        width: '12%',
        align: 'end',
        headerAlign: 'end',
        cell: (rule) => (
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<Pencil className="size-3.5" aria-hidden />}
            onClick={() => setEditing(rule)}
          >
            编辑
          </Button>
        ),
      },
    ],
    [bots, projects, rules.setRuleEnabled, rules.updatingRuleId, schedulerBots],
  );

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="icon" aria-label="返回待办" onClick={onBack}>
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <h2 className="m-0 text-xl font-semibold leading-[30px] text-foreground">自动化</h2>
        <span className="text-xs text-muted-foreground">· 按 Dima 范围与自然语言条件自动创建待办</span>
      </div>
      <div className="flex items-center justify-end">
        <Button
          leftIcon={<Plus className="size-3.5" aria-hidden />}
          disabled={!spaceId || bots.length === 0 || schedulerBots.length === 0}
          onClick={() => setEditing('create')}
        >
          创建自动化
        </Button>
      </div>

      {rules.error ? (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-xs text-destructive">
          <span>{rules.error}</span>
          <Button size="sm" variant="outline" onClick={() => void rules.refresh()}>
            重试
          </Button>
        </div>
      ) : null}

      {rules.loading ? (
        <Spin tip="加载自动化中…" className="py-16" />
      ) : rules.rules.length === 0 ? (
        <Empty title="暂无自动化" description="创建规则后，可由外部 Agent 按配置扫描 Dima 工作项并创建待办。" />
      ) : (
        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <DataTable
            ariaLabel="Dima 自动化列表"
            columns={columns}
            rows={rules.rules}
            getRowKey={(rule) => rule.ruleId}
            minWidth="min-w-[920px]"
          />
        </div>
      )}

      <TodoAutomationEditor
        open={editing !== null}
        rule={editing === 'create' ? undefined : editing ?? undefined}
        projects={projects}
        projectsLoading={projectsLoading}
        bots={bots}
        schedulerBots={schedulerBots}
        saving={rules.saving || Boolean(editing !== 'create' && rules.updatingRuleId)}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
        onSave={async (input) => {
          if (!spaceId) return false;
          const success = editing === 'create'
            ? await rules.createRule({ spaceId, ...input })
            : editing
              ? await rules.updateRule(editing, input)
              : false;
          if (success) {
            notifySuccess(editing === 'create' ? '自动化已创建' : '自动化已更新');
            setEditing(null);
          }
          return success;
        }}
      />
    </section>
  );
}
