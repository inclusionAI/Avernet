import { workspacePages } from '@/domain/taskEscortWorkspace';
import { readTaskEscortRoute, taskEscortSearch } from '@/services/taskEscortWorkspace/navigation';

describe('任务护航 URL 契约', () => {
  it('默认真实模式及七天大盘，未知页签与时间范围安全回退', () => {
    expect(readTaskEscortRoute('')).toEqual({ page: 'dashboard', period: '7d', mode: 'live' });
    expect(readTaskEscortRoute('?view=versions&period=forever&demo=true')).toEqual({
      page: 'dashboard',
      period: '7d',
      mode: 'live',
    });
  });
  it.each(workspacePages)('旧 demo=1 在 %s 视图仍解析为真实模式', (page) => {
    expect(readTaskEscortRoute(`?demo=1&view=${page}&period=today`)).toEqual({
      page,
      period: 'today',
      mode: 'live',
    });
    expect(readTaskEscortRoute('?demo=0').mode).toBe('live');
    expect(readTaskEscortRoute('?view=efficiency').page).toBe('dashboard');
  });
  it.each(['live', 'demo'] as const)('视图切换保留宿主 query 并无条件移除旧 demo，兼容 mode=%s 入参', (mode) => {
    const next = taskEscortSearch('?host=desktop&demo=1&demo=0&view=dashboard', { page: 'runs', period: '30d', mode });
    const query = new URLSearchParams(next);
    expect(query.get('host')).toBe('desktop');
    expect(query.get('demo')).toBeNull();
    expect(readTaskEscortRoute(next)).toEqual({ page: 'runs', period: '30d', mode: 'live' });
  });
});
