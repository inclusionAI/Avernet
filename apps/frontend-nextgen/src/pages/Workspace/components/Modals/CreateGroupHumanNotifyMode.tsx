import type { HumanMentionNotifyMode } from '@/domain/collaboration';
import { useEffect, useRef, useState } from 'react';
import { GroupLeaderSelect } from './GroupLeaderSelect';

interface CreateGroupHumanNotifyModeProps {
  value: HumanMentionNotifyMode;
  onChange: (value: HumanMentionNotifyMode) => void;
}

export function CreateGroupHumanNotifyMode({ value, onChange }: CreateGroupHumanNotifyModeProps) {
  return (
    <GroupLeaderSelect
      id="create-group-human-notify-mode"
      label="@Human 通知来源"
      value={value}
      options={[
        { id: 'all', name: '所有成员' },
        { id: 'driver_bot_only', name: '仅群主 Bot' },
        { id: 'none', name: '不通知' },
      ]}
      onChange={(next) => onChange(next as HumanMentionNotifyMode)}
    />
  );
}

/** 重开弹窗恢复 all；同一次建群中切换协作类型保留所选值。 */
export function useCreateGroupHumanNotifyMode(open: boolean, clearError: () => void) {
  const [value, setValue] = useState<HumanMentionNotifyMode>('all');
  const wasOpenRef = useRef(false);
  useEffect(() => {
    if (open && !wasOpenRef.current) setValue('all');
    wasOpenRef.current = open;
  }, [open]);
  const onChange = (next: HumanMentionNotifyMode) => {
    clearError();
    setValue(next);
  };
  return { value, onChange };
}
