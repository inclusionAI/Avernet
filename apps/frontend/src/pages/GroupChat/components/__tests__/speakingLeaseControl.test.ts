import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { SpeakingLease } from '../../utils/speakingLease';
import SpeakingLeaseControl from '../SpeakingLeaseControl';

const mockButtons: any[] = [];
jest.mock('@/components', () => ({
  Button: (props: any) => {
    mockButtons.push(props);
    return null;
  },
}));

const heldLease: SpeakingLease = {
  leaseId: 'lease-1',
  epoch: 3,
  expiresAtMs: 180_000,
  holderActorId: 'human_12345',
};

describe('speaking lease control', () => {
  beforeEach(() => {
    mockButtons.length = 0;
  });

  it('offers acquisition when no lease is held', () => {
    const onAcquire = jest.fn();
    renderToStaticMarkup(
      React.createElement(SpeakingLeaseControl, {
        status: 'unheld',
        onAcquire,
        onPreempt: jest.fn(),
        onRelease: jest.fn(),
      } as any),
    );
    expect(mockButtons[0].onClick).toBe(onAcquire);
    expect(mockButtons[0].children).toContain('获取发言权');
  });

  it('offers preemption when another human has the lease', () => {
    const onPreempt = jest.fn();
    const html = renderToStaticMarkup(
      React.createElement(SpeakingLeaseControl, {
        status: 'occupied',
        onAcquire: jest.fn(),
        onPreempt,
        onRelease: jest.fn(),
      } as any),
    );
    expect(html).toContain('发言权被占用');
    expect(mockButtons[0].onClick).toBe(onPreempt);
    expect(mockButtons[0].children).toContain('抢占发言权');
  });

  it('shows the held lease and exposes release', () => {
    const onRelease = jest.fn();
    const html = renderToStaticMarkup(
      React.createElement(SpeakingLeaseControl, {
        status: 'held',
        lease: heldLease,
        nowMs: 100_000,
        onAcquire: jest.fn(),
        onPreempt: jest.fn(),
        onRelease,
      } as any),
    );
    expect(html).toContain('发言权已获取');
    expect(html).toContain('剩余 80 秒');
    expect(mockButtons[0].onClick).toBe(onRelease);
    expect(mockButtons[0].children).toContain('释放');
  });
});
