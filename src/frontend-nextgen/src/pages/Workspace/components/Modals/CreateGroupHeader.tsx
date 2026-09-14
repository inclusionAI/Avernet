import { ModalHeader, ModalTitle } from '@/components/ui/Modal';

export function CreateGroupHeader() {
  return (
    <ModalHeader className="border-b border-border px-6 pb-4 pt-5">
      <ModalTitle className="m-0 shrink-0 text-base font-semibold text-foreground">发起协作</ModalTitle>
    </ModalHeader>
  );
}
