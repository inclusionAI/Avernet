import { ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';

export function CreateGroupHeader() {
  return (
    <ModalHeader className="col-span-full space-y-1 border-b border-border px-4 py-5 sm:px-7">
      <ModalTitle className="m-0 shrink-0 pr-8 text-sm font-semibold tracking-tight text-foreground">发起协作</ModalTitle>
      <ModalDescription className="pr-8 text-xs leading-5">设置协作方式，为团队选择合适的 Bot。</ModalDescription>
    </ModalHeader>
  );
}
