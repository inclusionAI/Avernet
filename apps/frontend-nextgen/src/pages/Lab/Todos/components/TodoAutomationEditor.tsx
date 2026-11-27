import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type { TodoAutoImportRule, TodoBotOption, TodoSourceProject } from '@/domain/todos/models';
import { useEffect, useState } from 'react';

interface TodoAutomationEditorProps {
  open: boolean;
  rule?: TodoAutoImportRule;
  projects: TodoSourceProject[];
  projectsLoading: boolean;
  bots: TodoBotOption[];
  saving: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (input: { sourceContainerIds: string[]; filterText: string; botId: string }) => Promise<boolean>;
}

export function TodoAutomationEditor({
  open,
  rule,
  projects,
  projectsLoading,
  bots,
  saving,
  onOpenChange,
  onSave,
}: TodoAutomationEditorProps) {
  const [containerIds, setContainerIds] = useState<string[]>([]);
  const [filterText, setFilterText] = useState('');
  const [botId, setBotId] = useState('');

  useEffect(() => {
    if (!open) return;
    setContainerIds(rule?.sourceContainerIds ?? []);
    setFilterText(rule?.filterText ?? '');
    setBotId(rule?.botId ?? bots[0]?.botId ?? '');
  }, [bots, open, rule]);

  const toggleProject = (projectId: string, checked: boolean) => {
    setContainerIds((current) =>
      checked ? [...new Set([...current, projectId])] : current.filter((item) => item !== projectId),
    );
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="lg" aria-describedby={undefined}>
        <ModalHeader>
          <ModalTitle>{rule ? '编辑自动化' : '新建自动化'}</ModalTitle>
          <ModalDescription>当前自动化使用自然语言条件，由外部 Agent 负责实际扫描和调度。</ModalDescription>
        </ModalHeader>
        <div className="space-y-4">
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium text-foreground">Dima 空间 / 项目</legend>
            {projectsLoading ? <p className="m-0 text-xs text-muted-foreground">加载中…</p> : null}
            <div className="grid max-h-48 gap-2 overflow-y-auto sm:grid-cols-2">
              {projects.map((project) => (
                <label key={project.id} className="flex cursor-pointer items-start gap-2 rounded-lg border border-border p-3 text-xs">
                  <Checkbox
                    checked={containerIds.includes(project.id)}
                    onCheckedChange={(checked) => toggleProject(project.id, checked)}
                  />
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-foreground">{project.name}</span>
                    <span className="mt-0.5 block text-[11px] text-muted-foreground">{project.id}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>导入条件</span>
            <Textarea
              value={filterText}
              onChange={(event) => setFilterText(event.target.value)}
              rows={4}
              placeholder="例如：导入需要研发处理且尚未完成的事项"
            />
            <span className="block text-xs font-normal text-muted-foreground">使用单一自由文本描述筛选条件。</span>
          </label>
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>执行 Bot</span>
            <Select value={botId || undefined} onValueChange={setBotId} disabled={bots.length === 0}>
              <SelectTrigger aria-label="选择自动导入执行 Bot">
                <SelectValue placeholder="请选择 Bot" />
              </SelectTrigger>
              <SelectContent>
                {bots.map((bot) => (
                  <SelectItem key={bot.botId} value={bot.botId}>
                    {bot.botName}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
        </div>
        <ModalFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            loading={saving}
            disabled={containerIds.length === 0 || !filterText.trim() || !botId}
            onClick={() => void onSave({ sourceContainerIds: containerIds, filterText: filterText.trim(), botId })}
          >
            保存
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
