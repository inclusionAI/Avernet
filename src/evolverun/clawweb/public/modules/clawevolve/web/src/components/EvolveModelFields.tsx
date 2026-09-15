import modelOptions from '../../../config/model-options.json'

export const EVOLVE_MODEL_OPTIONS: readonly string[] = modelOptions.models

export const EVOLVE_CUSTOM_MODEL = '__custom__'
// An empty selection means that the task does not override OpenClaw's model.
export const INITIAL_EVOLVE_MODEL = ''

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
  modelOptions = EVOLVE_MODEL_OPTIONS,
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
  modelOptions?: readonly string[]
  customPlaceholder?: string
}) {
  const options = [...new Set(modelOptions.filter(Boolean))]
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
          <option value="">默认模型</option>
          {options.map((model) => <option key={model} value={model}>{model}</option>)}
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
