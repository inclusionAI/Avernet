import { Segmented } from '@/components/ui/Segmented';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useState } from 'react';
import { ManagementAutomation } from './ManagementAutomation';
import { ManagementCallbacks, ManagementNotification } from './ManagementNotifications';
import { ManagementFacades, ManagementPermissions } from './ManagementPermissions';
import { ManagementSection } from './ManagementPrimitives';

type Setting = 'permissions' | 'facades' | 'automation' | 'notification' | 'callback';
const options: { value: Setting; label: string }[] = [
  { value: 'permissions', label: '权限' },
  { value: 'facades', label: '命令' },
  { value: 'automation', label: '自动化设置' },
  { value: 'notification', label: '通知' },
  { value: 'callback', label: '回调' },
];
/** Mounted only after explicit canView + canEdit. Privileged readers are lazy per panel. */
export function ManagementSettings({ context }: { context: TaskEscortParityContext }) {
  const [tab, setTab] = useState<Setting>('permissions');
  return (
    <ManagementSection title="工作流设置">
      <Segmented aria-label="工作流设置页签" className="flex-wrap" options={options} value={tab} onChange={setTab} />
      {tab === 'permissions' && <ManagementPermissions context={context} />}
      {tab === 'facades' && <ManagementFacades context={context} />}
      {tab === 'automation' && <ManagementAutomation context={context} />}
      {tab === 'notification' && <ManagementNotification context={context} />}
      {tab === 'callback' && <ManagementCallbacks context={context} />}
    </ManagementSection>
  );
}
