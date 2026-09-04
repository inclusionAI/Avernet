import {
  DEFAULT_ROUTINE_CRON,
  getRoutineScheduleLabel,
  isRoutineSchedulePreset,
  ROUTINE_SCHEDULE_PRESETS,
} from '@/services/botWorkshop/routineSchedule';

test('常用频率准确映射为后端支持的五段 Cron', () => {
  expect(DEFAULT_ROUTINE_CRON).toBe('0 9 * * *');
  expect(ROUTINE_SCHEDULE_PRESETS).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ value: '*/30 * * * *', label: '每 30 分钟' }),
      expect.objectContaining({ value: '0 18 * * 1-5', label: '工作日 18:00' }),
    ]),
  );
});

test('已有自定义 Cron 保持高级模式并给出可读说明', () => {
  expect(isRoutineSchedulePreset('15 10 * * 2')).toBe(false);
  expect(getRoutineScheduleLabel('15 10 * * 2')).toBe('自定义（15 10 * * 2）');
});
