import { Mail } from 'lucide-react';
import type { SettingsEditorProps } from './SettingsAutomation';
import type { TaskEscortWorkspaceController } from './types';
import { Button, Input, Switch } from './ui';

export function SettingsNotifications({
  settings,
  update,
  notice,
}: SettingsEditorProps & { notice: TaskEscortWorkspaceController['notice'] }) {
  return (
    <div className="mt-5 space-y-6">
      <div className="setting-row">
        <div>
          <label htmlFor="notifications" className="text-xs font-medium">
            待办与复发通知
          </label>
          <p className="mt-1.5 text-[11px] text-muted-foreground">在新增建议、等待验证或再次复发时发送提醒。</p>
        </div>
        <Switch
          id="notifications"
          checked={settings.notifications}
          onCheckedChange={(notifications) => update({ notifications })}
        />
      </div>
      <div className="max-w-xl">
        <label className="field-label" htmlFor="callback">
          回调地址
        </label>
        <Input
          id="callback"
          placeholder="https://example.com/hooks/task-guard"
          value={settings.webhook}
          onChange={(e) => update({ webhook: e.target.value })}
        />
        <p className="mt-2 text-[10px] leading-5 text-muted-foreground">演示仅保存文本，不向该地址发送请求。</p>
      </div>
      <div className="flex items-center gap-3 rounded-lg border border-border p-4">
        <Mail size={20} className="text-muted-foreground" />
        <div className="flex-1">
          <p className="text-xs font-medium">通知预览</p>
          <p className="mt-1 text-[11px] text-muted-foreground">查看「任务护航发现异常」通知的内容示例</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => notice('【演示通知】数据采集节点出现超时，影响 24 次运行。请前往问题与优化审核建议。')}
        >
          发送演示通知
        </Button>
      </div>
    </div>
  );
}
