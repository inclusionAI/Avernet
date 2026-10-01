import { Segmented } from '@/components/ui';
import type { WorkspaceView } from '@/domain/collaboration/availableViews';

interface WorkspacePrimaryTabsProps {
  value: WorkspaceView;
  options: WorkspaceView[];
  onChange: (value: WorkspaceView) => void;
}

/** 工作台二级视图切换：使用分段控件强化选中态，避免顶部导航过轻。 */
export function WorkspacePrimaryTabs({ value, options, onChange }: WorkspacePrimaryTabsProps) {
  return (
    <Segmented
      value={value}
      options={options.map((option) => ({
        value: option,
        label: option === 'chat' ? '对话' : '协作群',
      }))}
      onChange={onChange}
      aria-label="工作区类型"
      className="min-w-0 flex-1"
      inactiveOptionClassName="text-muted-foreground hover:text-foreground"
    />
  );
}
