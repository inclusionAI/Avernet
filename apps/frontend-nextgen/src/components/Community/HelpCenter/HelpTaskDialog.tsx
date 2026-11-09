import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import type { HelpBoard, HelpTaskDraft } from '@/domain/communityHelp/types';
import { useState } from 'react';
import { HelpTaskSourceFields } from './HelpTaskSourceFields';

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
    syncToDima: false,
    dimaProjectSpace: '',
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
            发布到 {board.name} ·{' '}
            {board.collaborationMode === 'open'
              ? '开放协作，不限定特定 Bot'
              : `使用本模版的 ${board.members.length} 个 Mock Bot`}
            ，仅演示协作流程。
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
          <HelpTaskSourceFields board={board} draft={draft} onChange={setDraft} />
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
