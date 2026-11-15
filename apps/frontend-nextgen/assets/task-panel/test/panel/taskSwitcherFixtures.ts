import { vi } from 'vitest';
import type { TaskPanelHost, TaskPanelDeclaration } from '../../src/contracts';
import type { TaskListRecord } from '../../src/data/taskClient';
import type { TaskDashboardResponse } from '../../src/panel/contract';

export const tab = (id: string): TaskPanelDeclaration => ({ id: `task-${id}`, type: 'taskPanel.TaskLoopView', title: `Title ${id}`, closable: true, params: { taskId: id } });
export const row = (id: string, session = 'shared', status = 'EXECUTING'): TaskListRecord => ({
  task_id: id, status, gmt_modified: '2026-10-20 10:00:00',
  execution_config: { main_session_id: session }, task_spec: { context: { title: `Title ${id}` } },
});
export function dashboard(id: string, node: Partial<TaskDashboardResponse['tasks'][number]> = {}): TaskDashboardResponse {
  return {
    task_id: id, run_id: 1, status: 'EXECUTING', needs_attention: false, task_type: 'dynamic', source_type: 'bot',
    owner_user_id: 'viewer', owner_bot_id: 'bot-owner',
    task_spec: { context: { title: `Dashboard ${id}`, background: '', extend_props: {} }, goal: { objective: '', acceptances: [] } },
    create_time: '2026-10-20 10:00:00', finish_time: null, loop_round: 1,
    progress: { total: 1, pending: 0, planning: 0, running: 1, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
    tasks: [{ node_id: 'n-first', task_id: id, sequence: 1, status: 'RUNNING',
      run_info: { run_mode: 'single_bot', assignee: 'bot-first', start_time: 1760000000000, end_time: 1760000060000,
        extend_props: { session_id: 'session-first-step' } }, ...node }], relations: [],
  } as TaskDashboardResponse;
}
export function makeHost(rows: readonly TaskListRecord[] = [row('t1'), row('t2')]): TaskPanelHost {
  return {
    getViewer: () => ({ userId: 'viewer' }), getConversation: () => ({ sessionId: 'shared', kind: 'bot' }),
    transport: { requestJson: vi.fn(async path => path.includes('/dashboard?')
      ? { data: dashboard(new URL(path, 'https://fixture.invalid').searchParams.get('task_id')!) }
      : { data: { items: path.includes('/by_session?') ? [{ task_id: 't1' }, { task_id: 't2' }] : rows } }) },
    history: { readPage: vi.fn(async () => ({ messages: [], nextCursor: null })) },
    artifacts: { readContent: vi.fn(), download: vi.fn() },
    navigate: vi.fn(async () => ({ status: 'ready' as const })), captureOrigin: vi.fn(() => ({ opaque: 'origin' })),
    restoreOrigin: vi.fn(async () => ({ status: 'ready' as const })),
    present: vi.fn(), preservePanel: vi.fn(), recordInteraction: vi.fn(),
  };
}
