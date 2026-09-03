import { Badge, Button, Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui';
import type { IdentityView } from '@/domain/collaboration';
import type { CollaborationBotView } from '@/services/workspace/collaborationCandidateService';
import { useEffect, useMemo, useState } from 'react';
import { useGroupCollaborationPicker } from '../../hooks/useGroupCollaborationPicker';
import { GroupParticipantPicker } from '../Modals/GroupParticipantPicker';

export interface AddMemberDialogProps {
  open: boolean;
  existingIds: string[];
  activeIdentity?: IdentityView | null;
  onClose: () => void;
  onAddMany: (actorIds: string[]) => Promise<number>;
}

function selectedBotsByIds(
  picker: ReturnType<typeof useGroupCollaborationPicker>,
  selectedIds: string[],
): Array<{ id: string; name: string }> {
  const map = new Map<string, CollaborationBotView>();
  [...picker.friends, ...picker.mine, ...picker.candidates].forEach((bot) => map.set(bot.id, bot));
  return selectedIds.map((id) => map.get(id)).filter((bot): bot is CollaborationBotView => Boolean(bot));
}

/** 添加成员弹窗：从当前身份的好友、自有 Bot 和可协作 Bot 中选择。 */
export function AddMemberDialog({ open, existingIds, activeIdentity, onClose, onAddMany }: AddMemberDialogProps) {
  const picker = useGroupCollaborationPicker(activeIdentity?.id, open, activeIdentity?.kind === 'user');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) setSelectedIds([]);
  }, [open]);

  const filteredPicker = useMemo(() => {
    const removeExisting = (bots: CollaborationBotView[]) => bots.filter((bot) => !existingIds.includes(bot.id));
    return {
      ...picker,
      friends: removeExisting(picker.friends),
      mine: removeExisting(picker.mine),
      candidates: removeExisting(picker.candidates),
    };
  }, [existingIds, picker]);

  const selectedOptions = useMemo(() => selectedBotsByIds(picker, selectedIds), [picker, selectedIds]);

  const toggle = (id: string) => {
    setSelectedIds((current) => (current.includes(id) ? current.filter((x) => x !== id) : [...current, id]));
  };

  const handleConfirm = async () => {
    if (selectedIds.length === 0 || submitting) return;
    setSubmitting(true);
    const success = await onAddMany(selectedIds);
    setSubmitting(false);
    if (success === selectedIds.length) onClose();
  };

  return (
    <Modal open={open} onOpenChange={(next) => !submitting && !next && onClose()}>
      <ModalContent size="lg" closeLabel="关闭添加成员弹窗">
        <ModalHeader>
          <ModalTitle className="m-0 flex items-center gap-2 text-base font-semibold text-foreground">
            添加成员
            <Badge tone="neutral">已选 {selectedIds.length}</Badge>
          </ModalTitle>
        </ModalHeader>

        <GroupParticipantPicker
          picker={filteredPicker}
          selectedIds={selectedIds}
          selectedOptions={selectedOptions}
          showMineTab={activeIdentity?.kind === 'user'}
          cardMode
          onToggle={toggle}
          excludeId={activeIdentity?.id}
        />

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button onClick={() => void handleConfirm()} disabled={selectedIds.length === 0 || submitting}>
            {submitting ? '处理中…' : '确认添加'}
          </Button>
        </div>
      </ModalContent>
    </Modal>
  );
}

export default AddMemberDialog;
