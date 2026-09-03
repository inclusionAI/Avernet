/**
 * AgentCoding 应用模板配置入口。
 * 表单主体直接迁移自 open-claw 的 AppCodingConfigForm，组件只在这里适配
 * TeamClaw 的 draft value/onChange 契约；字段组件本身保留旧版实现。
 */
import { forwardRef, useCallback, useRef } from 'react';
import LegacyAppCodingConfigForm, { type AppCodingConfigFormRef } from './legacy/appcoding/AppCodingConfigForm';
type TemplateConfig = Record<string, any>;

interface Props {
  value: Record<string, unknown>;
  initialConfig?: Record<string, unknown>;
  disabled?: boolean;
  onChange: (value: Record<string, unknown>) => void;
  onValidationChange?: (error?: string) => void;
}

export const ApplicationCodingConfigForm = forwardRef<AppCodingConfigFormRef, Props>(
  ({ value, initialConfig, disabled, onChange, onValidationChange }, ref) => {
    // 旧版表单是“内部状态 + initialConfig 回填”模型。保留其生命周期语义，
    // 避免用户输入时父层 value 变化触发整张表单重新初始化。
    const initialConfigRef = useRef<TemplateConfig>({
      ...(initialConfig as TemplateConfig | undefined),
      ...(value as TemplateConfig),
    });
    // 旧版表单会把 onValidationChange 放进 effect 依赖；用 ref 保持适配回调恒定，
    // 避免父层状态更新后旧版 effect 被无意义地重新触发。
    const onValidationChangeRef = useRef(onValidationChange);
    onValidationChangeRef.current = onValidationChange;
    const handleValidationChange = useCallback((error: string | null) => {
      onValidationChangeRef.current?.(error ?? undefined);
    }, []);
    return (
      <LegacyAppCodingConfigForm
        ref={ref}
        initialConfig={initialConfigRef.current}
        disabled={disabled}
        onChange={onChange}
        onValidationChange={handleValidationChange}
      />
    );
  },
);
