import { ShieldCheck } from 'lucide-react';
import type { TaskEscortSettings } from './types';
import { Badge, SelectField, Switch } from './ui';

export type SettingsEditorProps = {
  settings: TaskEscortSettings;
  update: (patch: Partial<TaskEscortSettings>) => void;
};

export function SettingsAutomation({ settings, update }: SettingsEditorProps) {
  return (
    <div className="mt-5">
      <div className="mb-6 flex items-center gap-3 rounded-lg border border-primary/15 bg-primary/[.035] p-4">
        <span className="grid size-10 place-items-center rounded-xl bg-primary/10 text-primary">
          <ShieldCheck size={23} />
        </span>
        <div>
          <h3 className="text-sm font-medium">自动化护航</h3>
          <p className="mt-1 text-xs text-muted-foreground">持续观察运行，在需要时给出有证据的优化建议</p>
        </div>
        <Badge tone={settings.enabled ? 'success' : 'neutral'} className="ml-auto">
          {settings.enabled ? '已开启' : '已暂停'}
        </Badge>
      </div>
      <div className="divide-y divide-border">
        {[
          {
            key: 'enabled',
            label: '开启任务护航',
            description: '持续收集运行信号，维护全局运行健康视图。',
          },
          {
            key: 'autoAnalyze',
            label: '运行异常后自动分析',
            description: '自动聚合失败证据、识别根因并生成优化建议，不自动应用修改。',
          },
          {
            key: 'humanReview',
            label: '应用修改前人工审核',
            description: '建议与配置差异经过确认后才能应用，保留人工决策。',
          },
          {
            key: 'recurrence',
            label: '复发问题持续跟踪',
            description: '修复后继续观察同类问题，复发时重新进入待办队列。',
          },
        ].map((item) => (
          <div className="setting-row" key={item.key}>
            <div>
              <label htmlFor={`setting-${item.key}`} className="text-xs font-medium">
                {item.label}
              </label>
              <p className="mt-1.5 text-[11px] leading-5 text-muted-foreground">{item.description}</p>
            </div>
            <Switch
              id={`setting-${item.key}`}
              aria-label={item.label}
              checked={Boolean(settings[item.key as keyof TaskEscortSettings])}
              onCheckedChange={(checked) => update({ [item.key]: checked })}
            />
          </div>
        ))}
        <div className="setting-row">
          <div>
            <p className="text-xs font-medium">异常识别灵敏度</p>
            <p className="mt-1.5 text-[11px] text-muted-foreground">在及时发现与减少噪音之间取得平衡。</p>
          </div>
          <SelectField
            label="异常识别灵敏度"
            value={settings.sensitivity}
            onChange={(sensitivity) => update({ sensitivity })}
            options={[
              { value: 'balanced', label: '均衡 · 推荐' },
              { value: 'high', label: '高灵敏' },
              { value: 'low', label: '低噪音' },
            ]}
          />
        </div>
      </div>
      <div className="mt-5 rounded-lg bg-muted/50 p-3 text-[11px] leading-5 text-muted-foreground">
        自动分析不会自动发布配置。关闭人工审核仍不会在此 DEMO
        中触发任何自动写入；真实权限与审批策略将在接入阶段单独评审。
      </div>
    </div>
  );
}
