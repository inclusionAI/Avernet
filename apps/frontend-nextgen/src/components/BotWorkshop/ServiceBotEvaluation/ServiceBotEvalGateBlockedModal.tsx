import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';

interface ServiceBotEvalGateBlockedModalProps {
  open: boolean;
  onClose: () => void;
  onGoEvaluation: () => void;
}

export function ServiceBotEvalGateBlockedModal({ open, onClose, onGoEvaluation }: ServiceBotEvalGateBlockedModalProps) {
  return (
    <Modal open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <ModalContent size="sm" showClose={false}>
        <ModalHeader>
          <ModalTitle>无法推进发布</ModalTitle>
          <ModalDescription>评测未通过，无法推进发布</ModalDescription>
        </ModalHeader>
        <p className="m-0 text-sm leading-6 text-muted-foreground">
          请完成评测数据上报认证，或重新发起评测后再推进发布。
        </p>
        <ModalFooter>
          <Button variant="secondary" onClick={onClose}>
            稍后处理
          </Button>
          <Button onClick={onGoEvaluation}>
            去评测认证
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
