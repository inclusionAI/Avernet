export const ROUTINE_SCHEDULE_PRESETS = [
  { value: '*/30 * * * *', label: '每 30 分钟' },
  { value: '30 8 * * *', label: '每天 08:30' },
  { value: '0 9 * * *', label: '每天 09:00' },
  { value: '0 18 * * 1-5', label: '工作日 18:00' },
  { value: '0 9 * * 1', label: '每周一 09:00' },
  { value: '0 8 1 * *', label: '每月 1 日 08:00' },
] as const;

export const DEFAULT_ROUTINE_CRON = ROUTINE_SCHEDULE_PRESETS[2].value;

export function getRoutineScheduleLabel(cron: string) {
  return ROUTINE_SCHEDULE_PRESETS.find((preset) => preset.value === cron)?.label ?? `自定义（${cron}）`;
}

export function isRoutineSchedulePreset(cron: string) {
  return ROUTINE_SCHEDULE_PRESETS.some((preset) => preset.value === cron);
}
