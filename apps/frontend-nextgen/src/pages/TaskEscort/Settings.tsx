import { Bell, History, Save, Users, Zap } from 'lucide-react';
import { useState } from 'react';
import { SettingsAutomation } from './SettingsAutomation';
import { SettingsPermissions, SettingsVersions } from './SettingsManagementPanels';
import { SettingsNotifications } from './SettingsNotifications';
import type { TaskEscortSettings, ViewProps } from './types';
import { Badge, Button, Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle, Panel } from './ui';

type SettingsTab = 'automation' | 'notifications' | 'permissions' | 'versions';
const tabs: { id: SettingsTab; label: string; icon: typeof Zap }[] = [
  { id: 'automation', label: '自动化设置', icon: Zap },
  { id: 'notifications', label: '通知与回调', icon: Bell },
  { id: 'permissions', label: '权限管理', icon: Users },
  { id: 'versions', label: '版本历史', icon: History },
];
export function Settings({ demo }: ViewProps) {
  const [tab, setTab] = useState<SettingsTab>('automation');
  const [rollback, setRollback] = useState<string | null>(null);
  const [currentVersion, setCurrentVersion] = useState('v2.4');
  const dirty = demo.settingsDirty;
  const update = (patch: Partial<TaskEscortSettings>) => {
    demo.updateSettings(patch);
  };
  const save = () => {
    demo.saveSettings();
  };
  return (
    <div className="space-y-5">
      <Panel
        title="工作流管理"
        description="工作流的全局配置、自动化策略与版本记录"
        action={<Badge tone="primary">全局管理</Badge>}
      >
        <div className="flex flex-wrap gap-1 border-b border-border pb-3" role="tablist" aria-label="管理设置分类">
          {tabs.map((item) => (
            <Button
              key={item.id}
              role="tab"
              aria-selected={tab === item.id}
              variant="ghost"
              className={tab === item.id ? 'bg-primary/5 text-primary' : ''}
              onClick={() => setTab(item.id)}
            >
              <item.icon size={14} />
              {item.label}
            </Button>
          ))}
        </div>
        {tab === 'automation' && <SettingsAutomation settings={demo.settings} update={update} />}
        {tab === 'notifications' && (
          <SettingsNotifications settings={demo.settings} update={update} notice={demo.notice} />
        )}
        {tab === 'permissions' && <SettingsPermissions />}
        {tab === 'versions' && <SettingsVersions currentVersion={currentVersion} onRollback={setRollback} />}
      </Panel>
      <div className="sticky bottom-0 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card/95 px-5 py-3 backdrop-blur">
        <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <span className={`size-1.5 rounded-full ${dirty ? 'bg-warning' : 'bg-success'}`} />
          {dirty ? '有未保存的演示设置' : '设置已同步到当前演示状态'}
        </span>
        <Button onClick={save}>
          <Save size={14} />
          保存设置
        </Button>
      </div>
      <Modal
        open={rollback !== null}
        onOpenChange={(open) => {
          if (!open) setRollback(null);
        }}
      >
        <ModalContent size="sm">
          <ModalHeader>
            <ModalTitle>确认模拟回滚到 {rollback}？</ModalTitle>
            <ModalDescription>只改变当前演示的版本标识，不恢复或覆盖服务器配置，也不会触发真实部署。</ModalDescription>
          </ModalHeader>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setRollback(null)}>
              取消
            </Button>
            <Button
              onClick={() => {
                if (rollback) setCurrentVersion(rollback);
                setRollback(null);
                demo.notice('模拟回滚完成 · 未修改真实工作流');
              }}
            >
              确认模拟回滚
            </Button>
          </div>
        </ModalContent>
      </Modal>
    </div>
  );
}
