import { ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { IdentityView } from '@/domain/collaboration';

interface Props {
  activeIdentity?: IdentityView | null;
  activeIdentityDisplayName: string;
}

export function CreateGroupHeader({ activeIdentity, activeIdentityDisplayName }: Props) {
  return (
    <ModalHeader className="border-b border-border px-6 pb-4 pt-5">
      <div className="flex min-w-0 items-center gap-2">
        <ModalTitle className="m-0 shrink-0 text-base font-semibold text-foreground">发起协作</ModalTitle>
        {activeIdentity && (
          <>
            <span className="text-xs text-muted-foreground">为</span>
            <span className="shrink-0 rounded-full border border-primary/20 bg-primary/10 px-2 py-0.5 text-[11px] text-primary">
              {activeIdentity.kind === 'bot' ? 'Bot' : '用户'}
            </span>
            <span className="max-w-44 truncate rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
              {activeIdentityDisplayName}
            </span>
          </>
        )}
      </div>
    </ModalHeader>
  );
}
