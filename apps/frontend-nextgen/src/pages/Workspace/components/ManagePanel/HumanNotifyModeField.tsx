import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui';
import type { GroupUpdatePatch, GroupView, HumanMentionNotifyMode } from '@/domain/collaboration';
import type { DomainResult } from '@/services/workspace/identityService';
import { useState } from 'react';

/** @Human 通知来源（后端 HumanMentionNotifyMode）：决定 @Human 消息向外部渠道（钉钉等）通知的范围。 */
const HUMAN_MENTION_NOTIFY_OPTIONS: Array<{ value: HumanMentionNotifyMode; label: string }> = [
  { value: 'all', label: '所有成员' },
  { value: 'driver_bot_only', label: '仅群主 Bot' },
  { value: 'none', label: '不通知' },
];

export interface HumanNotifyModeFieldProps {
  /** 当前值：来自群详情（human_mention_notify_mode 回填）；旧后端缺省时按服务端存量默认 all 兜底展示。 */
  value: HumanMentionNotifyMode | undefined;
  canManage: boolean;
  /** 走既有 onUpdate({ humanMentionNotifyMode }) 群更新链路（useGroupManagement.updateGroup → PATCH groups/{id}）。 */
  onUpdate: (patch: GroupUpdatePatch) => Promise<DomainResult<GroupView> | null> | void;
}

/** 群管理·基础信息卡的「@Human 通知来源」下拉：值受控自群详情，写入失败不落本地值，写入中禁用防重复提交。 */
export function HumanNotifyModeField({ value, canManage, onUpdate }: HumanNotifyModeFieldProps) {
  const [saving, setSaving] = useState(false);
  const currentValue: HumanMentionNotifyMode = value ?? 'all';

  const handleChange = async (next: HumanMentionNotifyMode) => {
    if (!canManage || saving) return;
    setSaving(true);
    try {
      await onUpdate({ humanMentionNotifyMode: next });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-3">
      {/* 标签/选中值对齐基础信息卡口径；仅改实例 className，不动 Select 基座。 */}
      <p className="m-0 mb-1 text-xs font-medium text-foreground">@Human 通知来源</p>
      <Select
        value={currentValue}
        onValueChange={(next) => void handleChange(next as HumanMentionNotifyMode)}
        disabled={!canManage || saving}
      >
        <SelectTrigger aria-label="@Human 通知来源" className="w-full font-normal text-muted-foreground">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {HUMAN_MENTION_NOTIFY_OPTIONS.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}