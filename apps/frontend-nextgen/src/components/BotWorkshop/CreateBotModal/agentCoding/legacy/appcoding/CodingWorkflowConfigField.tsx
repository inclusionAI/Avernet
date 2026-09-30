import type { WorkflowItem } from '@/services/botWorkshop/agentCodingLegacyService';
import { getWorkflows } from '@/services/botWorkshop/agentCodingLegacyService';
import React, { useEffect, useState } from 'react';
import WorkflowSelect from './WorkflowSelect';

export interface CodingWorkflowConfigFieldProps {
  value: WorkflowItem | null;
  onChange: (workflow: WorkflowItem | null) => void;
  disabled?: boolean;
  required?: boolean;
  label?: string;
  placeholder?: string;
  className?: string;
  options?: WorkflowItem[];
  loading?: boolean;
}

export const CodingWorkflowConfigField: React.FC<CodingWorkflowConfigFieldProps> = ({
  value,
  onChange,
  disabled = false,
  required = true,
  label = '研发工作流',
  placeholder = '选择此应用的研发工作流',
  className,
  options,
  loading,
}) => {
  const [internalOptions, setInternalOptions] = useState<WorkflowItem[]>([]);
  const [internalLoading, setInternalLoading] = useState(false);
  const usingExternalOptions = options !== undefined;

  useEffect(() => {
    if (usingExternalOptions) return;
    let cancelled = false;
    const fetchWorkflows = async () => {
      setInternalLoading(true);
      try {
        const data = await getWorkflows();
        if (!cancelled) setInternalOptions(data);
      } catch (err) {
        console.error('[CodingWorkflowConfigField] Failed to load workflows:', err);
      } finally {
        if (!cancelled) setInternalLoading(false);
      }
    };
    fetchWorkflows();
    return () => {
      cancelled = true;
    };
  }, [usingExternalOptions]);

  return (
    <div className={className || 'space-y-1.5'}>
      <label className="flex items-center gap-1 text-xs font-semibold text-slate-600">
        {label}
        {required ? (
          <span className="text-red-500 ml-0.5">*</span>
        ) : (
          <span className="text-slate-400 font-normal">（可选）</span>
        )}
      </label>
      <WorkflowSelect
        value={value}
        options={usingExternalOptions ? options || [] : internalOptions}
        loading={loading ?? internalLoading}
        disabled={disabled}
        onChange={onChange}
        placeholder={placeholder}
      />
    </div>
  );
};

export default CodingWorkflowConfigField;
