// SessionItem 的两个行级弹窗：删除确认（AlertDialog）与标题编辑（Modal）。
// 弹窗状态（标题草稿/提交中）仍持有在 SessionItem，这里只负责呈现与触点转发。
import { Button, Input } from '@/components/ui';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/AlertDialog';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';

export function SessionItemDeleteDialog({
  open,
  onOpenChange,
  title,
  deleting,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  deleting: boolean;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除会话</AlertDialogTitle>
          <AlertDialogDescription>
            删除后该会话及其消息将无法恢复，确定删除“{title}”吗？
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm} disabled={deleting}>
            {deleting ? '删除中…' : '确认删除'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function SessionItemRenameDialog({
  open,
  onOpenChange,
  draft,
  onDraftChange,
  saving,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  draft: string;
  onDraftChange: (value: string) => void;
  saving: boolean;
  onConfirm: () => void;
}) {
  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="sm" showClose>
        <ModalHeader>
          <ModalTitle>编辑会话标题</ModalTitle>
        </ModalHeader>
        <Input value={draft} onChange={(event) => onDraftChange(event.target.value)} aria-label="会话标题" />
        <ModalFooter>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)} disabled={saving}>
            取消
          </Button>
          <Button variant="default" size="sm" onClick={onConfirm} disabled={saving || !draft.trim()}>
            保存
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}