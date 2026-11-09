import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type { HelpBoard, HelpSource, HelpTaskDraft } from '@/domain/communityHelp/types';
import { useState } from 'react';

export function HelpTaskDialog({
  board,
  onClose,
  onCreate,
}: {
  board: HelpBoard;
  onClose: () => void;
  onCreate: (draft: HelpTaskDraft) => boolean;
}) {
  const [draft, setDraft] = useState<HelpTaskDraft>({
    title: '',
    description: '',
    source: board.sources[0],
    sourceRef: '',
  });
  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>新建求助任务</ModalTitle>
          <ModalDescription>
            发布到 {board.name} · 使用本版面的 {board.members.length} 个 Mock Bot，仅演示协作流程。
          </ModalDescription>
        </ModalHeader>
        <form
          className="space-y-5"
          onSubmit={(event) => {
            event.preventDefault();
            if (onCreate(draft)) onClose();
          }}
        >
          <label className="block space-y-2 text-xs font-medium">
            <span>
              任务标题 <span className="text-destructive">*</span>
            </span>
            <Input
              required
              autoFocus
              maxLength={120}
              value={draft.title}
              onChange={(event) => setDraft({ ...draft, title: event.target.value })}
              placeholder="简要描述需要协作解决的问题"
            />
          </label>
          <label className="block space-y-2 text-xs font-medium">
            <span>
              任务说明 <span className="text-destructive">*</span>
            </span>
            <Textarea
              required
              rows={5}
              maxLength={5000}
              value={draft.description}
              onChange={(event) => setDraft({ ...draft, description: event.target.value })}
              placeholder="补充背景、复现步骤、期望结果及验收标准…"
            />
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <label htmlFor="help-task-source" className="text-xs font-medium">
                任务来源 <span className="text-destructive">*</span>
              </label>
              <Select
                value={draft.source}
                onValueChange={(value) => setDraft({ ...draft, source: value as HelpSource })}
              >
                <SelectTrigger id="help-task-source">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {board.sources.map((source) => (
                    <SelectItem key={source} value={source}>
                      {source}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <label className="space-y-2 text-xs font-medium">
              <span>来源任务编号（选填）</span>
              <Input
                maxLength={120}
                value={draft.sourceRef}
                onChange={(event) => setDraft({ ...draft, sourceRef: event.target.value })}
                placeholder="例如：DIMA-1042 / TD-2086"
              />
            </label>
          </div>
          <div className="rounded-lg bg-muted/50 p-3 text-xs leading-6 text-muted-foreground">
            {draft.source === 'Dima'
              ? 'Dima 来源：登记由 Dima 提供的求助任务。'
              : 'Task Discover 来源：登记由任务发现模块识别的问题。'}
            这里只保存来源标签与编号，不读取或同步外部任务。
          </div>
          <ModalFooter>
            <Button variant="outline" onClick={onClose}>
              取消
            </Button>
            <Button type="submit">创建 Mock 任务</Button>
          </ModalFooter>
        </form>
      </ModalContent>
    </Modal>
  );
}
