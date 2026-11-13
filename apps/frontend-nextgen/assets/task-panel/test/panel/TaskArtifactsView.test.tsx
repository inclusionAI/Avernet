import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TaskArtifactsView } from '../../src/panel/TaskArtifactsView';
import { TaskPanelHostContext } from '../../src/panel/hostContext';
import type { TaskPanelHost } from '../../src/contracts';

const dashboard = (id = 'task-1') => ({ code: 200000, data: {
  task_id: id, status: 'DONE', owner_user_id: 'artifact-owner', owner_bot_id: 'owner-bot',
  task_type: 'dynamic', source_type: 'bot', task_spec: {}, relations: [],
  tasks: [{ node_id: 'node-1', task_id: id, status: 'DONE', task_spec: {}, artifacts: [{ artifact_id: 'file-1', artifact_name: 'report.md', kind: 'file',
    content: { storage_type: 'object_file', storage_id: 'stable-key', file_name: 'report.md', file_type: 'text/markdown' }, created_at: 1792557600000 }] }],
} });
function makeHost(requestJson: TaskPanelHost['transport']['requestJson']): TaskPanelHost {
  return {
    getViewer: () => ({ userId: 'viewer-not-owner' }), getConversation: () => null,
    transport: { requestJson }, history: { readPage: vi.fn() },
    artifacts: { readContent: vi.fn(async () => new Blob(['# Report content'])), download: vi.fn(async () => {}) },
    navigate: vi.fn(), captureOrigin: vi.fn(), restoreOrigin: vi.fn(), present: vi.fn(),
    preservePanel: vi.fn(), recordInteraction: vi.fn(),
  };
}

describe('complete shared TaskArtifactsView', () => {
  it('owns loading, fetch configuration and list rendering', async () => {
    let resolve!: (value: unknown) => void;
    const request = vi.fn(async (path: string) => path.includes('/list?') ? { data: [] } : new Promise(done => { resolve = done; }));
    const host = makeHost(request);
    render(<TaskPanelHostContext.Provider value={host}><TaskArtifactsView taskId="task-1" ownerUserId="list-owner" apiBaseUrl="https://api.example/" taskApiBase="/read/tasks" /></TaskPanelHostContext.Provider>);
    expect(screen.getByRole('status')).toHaveTextContent('加载任务产物中');
    expect(request).toHaveBeenCalledWith('https://api.example/read/tasks/list?user_id=list-owner&page=1&page_size=100', expect.any(AbortSignal));
    expect(request).toHaveBeenCalledWith('https://api.example/read/tasks/dashboard?task_id=task-1&include_action_log=false', expect.any(AbortSignal));
    await act(async () => resolve(dashboard()));
    expect(await screen.findByText('report.md')).toBeInTheDocument();
    expect(screen.getByText('产物列表')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('owns error and retry without a host UI wrapper', async () => {
    let failed = true;
    const host = makeHost(vi.fn(async path => {
      if (path.includes('/list?')) return { data: [] };
      if (failed) throw new Error('Read unavailable');
      return dashboard();
    }));
    render(<TaskPanelHostContext.Provider value={host}><TaskArtifactsView taskId="task-1" ownerUserId="list-owner" apiBaseUrl="" /></TaskPanelHostContext.Provider>);
    expect(await screen.findByRole('alert')).toHaveTextContent('任务产物加载失败');
    expect(screen.getByRole('alert')).toHaveTextContent('Read unavailable');
    failed = false; fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('report.md')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('preserves owner access, preview portal, download and shared scope', async () => {
    const host = makeHost(vi.fn(async path => path.includes('/list?') ? { data: [] } : dashboard()));
    const portal = document.createElement('div'); document.body.appendChild(portal);
    try {
      render(<TaskPanelHostContext.Provider value={host}><TaskArtifactsView taskId="task-1" ownerUserId="list-owner" apiBaseUrl="" previewPortalContainer={portal} /></TaskPanelHostContext.Provider>);
      const preview = await screen.findByRole('button', { name: '预览产物 report.md' });
      expect(preview.closest('[data-task-panel-root]')).not.toBeNull();
      fireEvent.click(preview);
      expect(await within(portal).findByTestId('task-artifact-preview-drawer')).toHaveStyle({ position: 'absolute' });
      expect(await within(portal).findByRole('heading', { name: 'Report content' })).toBeInTheDocument();
      expect(host.artifacts.readContent).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ taskId: 'task-1', ownerUserId: 'artifact-owner', ownerBotId: 'owner-bot' }), expect.any(AbortSignal));
      fireEvent.click(within(portal).getByRole('button', { name: '下载文件' }));
      await waitFor(() => expect(host.artifacts.download).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ ownerUserId: 'artifact-owner' }), expect.any(AbortSignal)));
    } finally { portal.remove(); }
  });

  it('aborts old reads on task change and ignores late results', async () => {
    let finish!: (value: unknown) => void; let oldSignal!: AbortSignal;
    const host = makeHost(vi.fn(async (path, signal) => {
      if (path.includes('/list?')) return { data: [] };
      if (path.includes('task_id=old')) { oldSignal = signal; return new Promise(done => { finish = done; }); }
      return { code: 200000, data: { ...dashboard('new').data, tasks: [] } };
    }));
    const view = (id: string) => <TaskPanelHostContext.Provider value={host}><TaskArtifactsView taskId={id} ownerUserId="list-owner" apiBaseUrl="" /></TaskPanelHostContext.Provider>;
    const rendered = render(view('old')); rendered.rerender(view('new'));
    expect(oldSignal.aborted).toBe(true);
    expect(await screen.findByText('暂无产物')).toBeInTheDocument();
    await act(async () => finish(dashboard('old')));
    expect(screen.queryByText('report.md')).not.toBeInTheDocument();
    rendered.unmount();
    expect(vi.mocked(host.transport.requestJson).mock.calls.every(([, signal]) => signal.aborted)).toBe(true);
  });
});
