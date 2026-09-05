export const EVOLVE_MODEL_OPTIONS = [
  'Kimi-K2.5',
  'Kimi-K2.6',
  'GLM-5',
  'GLM-5.1',
  'GLM-5.2',
] as const

export const EVOLVE_CUSTOM_MODEL = '__custom__'
export const DEFAULT_EVOLVE_MODEL = EVOLVE_MODEL_OPTIONS[0]

export default function EvolveModelFields({
  choice,
  customValue,
  onChoiceChange,
  onCustomValueChange,
  selectAriaLabel,
  customAriaLabel,
  selectClassName,
  customClassName,
  inputClassName,
  customPlaceholder = '请输入 OpenAI-compatible 模型名',
}: {
  choice: string
  customValue: string
  onChoiceChange: (value: string) => void
  onCustomValueChange: (value: string) => void
  selectAriaLabel: string
  customAriaLabel: string
  selectClassName?: string
  customClassName?: string
  inputClassName: string
  customPlaceholder?: string
}) {
  return (
    <>
      <label className={selectClassName ?? 'text-xs font-medium text-gray-600'}>
        模型
        <select
          aria-label={selectAriaLabel}
          className={`${inputClassName} mt-1.5`}
          value={choice}
          onChange={(event) => onChoiceChange(event.target.value)}
        >
          {EVOLVE_MODEL_OPTIONS.map((model) => <option key={model} value={model}>{model}</option>)}
          <option value={EVOLVE_CUSTOM_MODEL}>自定义模型名称</option>
        </select>
      </label>
      {choice === EVOLVE_CUSTOM_MODEL && (
        <label className={customClassName ?? 'text-xs font-medium text-gray-600'}>
          自定义模型名称
          <input
            aria-label={customAriaLabel}
            className={`${inputClassName} mt-1.5`}
            maxLength={128}
            value={customValue}
            onChange={(event) => onCustomValueChange(event.target.value)}
            placeholder={customPlaceholder}
          />
        </label>
      )}
    </>
  )
}
