import { Segmented } from '@/components/ui/Segmented';
import type { CodeRepositoryTenant } from '@/services/botWorkshop/agentCodingLegacyService';
import { cn } from '@/utils/cn';

export const DEFAULT_CODE_REPOSITORY_TENANT: CodeRepositoryTenant = 'ant';

export const CODE_REPOSITORY_TENANT_OPTIONS: Array<{ value: CodeRepositoryTenant; label: string }> = [
  { value: 'ant', label: '主站' },
  { value: 'xiaojin', label: '消金' },
];

export interface CodeRepositoryTenantSwitchProps {
  value: CodeRepositoryTenant;
  onChange: (tenant: CodeRepositoryTenant) => void;
  disabled?: boolean;
  className?: string;
}

/** 代码仓库搜索租户切换：主站 AntCode / 消金代码平台。真实平台地址仍由 internal overlay 注入。 */
export function CodeRepositoryTenantSwitch({
  value,
  onChange,
  disabled = false,
  className,
}: CodeRepositoryTenantSwitchProps) {
  return (
    <div className={cn('shrink-0', className)}>
      <Segmented<CodeRepositoryTenant>
        value={value}
        onChange={onChange}
        options={CODE_REPOSITORY_TENANT_OPTIONS.map((option) => ({
          ...option,
          disabledReason: disabled ? '当前表单项已禁用' : undefined,
        }))}
        aria-label="代码仓库租户"
        className="scale-[0.8] opacity-75"
      />
    </div>
  );
}

export default CodeRepositoryTenantSwitch;
