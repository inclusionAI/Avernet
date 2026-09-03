import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type {
  FriendApprovalConfig,
  OrganizationPath,
  PublicAudience,
  PublicConfig,
} from '@/domain/collaborationPrivacy/types';

const audienceLabels: Record<PublicAudience, string> = { user: '其他用户', bot: '其他 Bot' };
const scopeLabels = { none: '不公开', all: '全部公开', restricted: '限制公开范围' } as const;

interface ScopeViewerBaseProps {
  open: boolean;
  onClose: () => void;
}

type ScopeViewerProps = ScopeViewerBaseProps &
  (
    | { kind: 'publication'; audience: PublicAudience; config: PublicConfig }
    | { kind: 'friendApproval'; config: FriendApprovalConfig }
  );

export function ScopeViewer(props: ScopeViewerProps) {
  const { open, onClose } = props;
  const publication = props.kind === 'publication';
  const paths: OrganizationPath[] = publication ? props.config.organizationPaths : props.config.exemptOrganizationPaths;
  const departmentNos = publication ? [] : props.config.exemptDepartmentNos ?? [];
  const count = paths.length || departmentNos.length;
  const title = publication ? `对${audienceLabels[props.audience]}公开范围` : '好友申请免审批范围';
  const description = publication
    ? '以下为当前已生效配置，不包含审批中的目标配置。'
    : '以下为当前已生效的好友申请免审批组织范围。';
  const statusLabel = publication ? scopeLabels[props.config.scope] : '部分组织免审批';
  const statusTone = publication
    ? props.config.scope === 'restricted'
      ? 'warning'
      : props.config.scope === 'all'
      ? 'success'
      : 'neutral'
    : 'warning';
  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContent>
        <ModalHeader>
          <ModalTitle>{title}</ModalTitle>
          <ModalDescription>{description}</ModalDescription>
        </ModalHeader>
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-sm text-foreground">
            <span>状态：</span>
            <Badge tone={statusTone}>{statusLabel}</Badge>
          </div>
          <p className="m-0 text-sm text-muted-foreground">范围数量：{count}</p>
          <ul className="m-0 space-y-2 p-0">
            {paths.map((path) => (
              <li
                key={path.join('\u0000')}
                className="list-none rounded-lg border border-border px-3 py-2 text-sm text-foreground"
              >
                {path.join(' / ')}
              </li>
            ))}
            {paths.length === 0 && departmentNos.length > 0 && (
              <li className="list-none rounded-lg border border-border px-3 py-2 text-sm text-muted-foreground">
                部门名称暂未加载，请关闭后刷新页面重试。
              </li>
            )}
          </ul>
        </div>
        <ModalFooter>
          <Button onClick={onClose}>知道了</Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
