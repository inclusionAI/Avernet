import { PillTabs } from '@/components/Common/PillTabs';

export type BotManagementTab = 'tc' | 'external';

/**
 * 「Bot 管理」页顶部的 TC Bot / 外部 Bot 分类切换。
 * dmore 稿（10/19 Bot管理首页）：胶囊式激活态（primary 12% 蓝底 + 蓝字 13px/500），
 * 未激活为灰字，不再使用底部下划线式 tab。
 */
export function BotManagementTabs({
  value,
  onChange,
  className,
}: {
  value: BotManagementTab;
  onChange: (tab: BotManagementTab) => void;
  className?: string;
}) {
  return (
    <PillTabs<BotManagementTab>
      label="Bot 类型"
      options={[{ value: 'tc', label: '内部Bot' }, { value: 'external', label: '外部 Bot' }]}
      value={value}
      onChange={onChange}
      className={className}
    />
  );
}
