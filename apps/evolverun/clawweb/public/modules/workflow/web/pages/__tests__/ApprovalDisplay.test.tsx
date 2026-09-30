// @vitest-environment jsdom
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Approval from '../Approval';

const card = {
  id: 1, flowId: 'flow-1', nodeId: 'review', workflowId: 'test', workflowTitle: '审核流程',
  approvalType: 'HUMAN_CONFIRM', message: '人工确认', cardFields: [{ label: '任务', value: 'T-1' }],
  approverIds: ['reviewer'], approverNames: ['审核人'], approvalPolicy: 'any', approvedBy: [], rejectedBy: [],
  status: 'pending', deliveryMode: 'card-web', createdAt: 100, resolvedAt: null,
};
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function show(data: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => data })));
  return render(<MemoryRouter initialEntries={['/approval/1?empId=reviewer']}><Routes><Route path='/approval/:id' element={<Approval />} /></Routes></MemoryRouter>);
}
describe('approval display configuration', () => {
  it('renders configured title, buttons and accessible note without default footer', async () => {
    show({ ...card, display: { title: '处置复核', confirmLabel: '执行处置', rejectLabel: '暂不执行', notePlaceholder: '填写复核说明', footer: '' } });
    expect(await screen.findByText('处置复核')).toBeTruthy();
    expect(screen.getByRole('button', { name: '执行处置' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '暂不执行' })).toBeTruthy();
    expect(screen.getByRole('textbox', { name: '填写复核说明' })).toBeTruthy();
    expect(screen.queryByText('工作流审批')).toBeNull();
  });
  it('shows configured resolved text and removes action buttons', async () => {
    show({ ...card, status: 'approved', approvedBy: ['reviewer'], resolvedAt: 145, display: { approvedText: '已完成处置复核' } });
    expect((await screen.findAllByText('已完成处置复核')).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: '确认执行' })).toBeNull();
  });

  it('opens a historical empId/corpId link and resolves with the authenticated ClawWeb session', async () => {
    let resolved = false;
    const requests: Array<{ url: string; body?: Record<string, unknown> }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      requests.push({ url, body });
      if (url.endsWith('/resolve/session')) {
        resolved = true;
        return { ok: true, json: async () => ({ ok: true, status: 'approved' }) };
      }
      return { ok: true, json: async () => ({ ...card, status: resolved ? 'approved' : 'pending' }) };
    }));

    render(<MemoryRouter initialEntries={['/approval/1?empId=legacy-reviewer&corpId=legacy-corp']}><Routes><Route path='/approval/:id' element={<Approval />} /></Routes></MemoryRouter>);
    await userEvent.click(await screen.findByRole('button', { name: '确认执行' }));

    const resolveRequest = requests.find((request) => request.url.endsWith('/resolve/session'));
    expect(resolveRequest?.body).toEqual({ action: 'approve' });
    expect(requests.some((request) => request.url.endsWith('/auth/dingtalk/config'))).toBe(false);
    expect(requests.some((request) => request.url.endsWith('/resolve'))).toBe(false);
    expect(requests.some((request) => request.url.includes('empId='))).toBe(false);
    expect(requests.some((request) => request.url.includes('corpId='))).toBe(false);
  });

  it('shows the server identity rejection instead of a generic HTTP label', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/resolve/session')) {
        return { ok: false, status: 403, json: async () => ({ error: 'Forbidden', message: '您不是此审批的授权审批人' }) };
      }
      return { ok: true, json: async () => card };
    }));

    render(<MemoryRouter initialEntries={['/approval/1']}><Routes><Route path='/approval/:id' element={<Approval />} /></Routes></MemoryRouter>);
    await userEvent.click(await screen.findByRole('button', { name: '确认执行' }));
    expect(await screen.findByText('操作失败: 您不是此审批的授权审批人')).toBeTruthy();
  });
});
