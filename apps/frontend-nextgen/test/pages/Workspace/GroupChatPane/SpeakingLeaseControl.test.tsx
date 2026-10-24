/** @jest-environment jsdom */
import type { SpeakingLease } from '@/domain/collaboration/speakingLease';
import {
  SpeakingLeaseControl,
  SpeakingLeaseStrip,
} from '@/pages/Workspace/components/GroupChatPane/SpeakingLeaseControl';
import type { GroupSpeakingLeaseModel } from '@/pages/Workspace/hooks/useSpeakingLease';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { render } from '@testing-library/react';

const mockButtons: Array<Record<string, unknown>> = [];
jest.mock('@/components/ui', () => ({
  Button: (props: Record<string, unknown>) => {
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

describe('SpeakingLeaseControl', () => {
  beforeEach(() => {
    mockButtons.length = 0;
  });

  it('offers acquisition when no lease is held', () => {
    const onAcquire = jest.fn();
    const { container } = render(
      <SpeakingLeaseControl status="unheld" onAcquire={onAcquire} onPreempt={jest.fn()} onRelease={jest.fn()} />,
    );
    expect(container.textContent).toContain('caller模式');
    expect(container.textContent).toContain('获取caller身份发言');
    expect(mockButtons[0].onClick).toBe(onAcquire);
    expect(mockButtons[0].children).toContain('获取发言权');
    expect(mockButtons[0].variant).toBe('ghost');
  });

  it('offers preemption when another human has the lease', () => {
    const onPreempt = jest.fn();
    const { container } = render(
      <SpeakingLeaseControl status="occupied" onAcquire={jest.fn()} onPreempt={onPreempt} onRelease={jest.fn()} />,
    );
    expect(container.textContent).toContain('caller模式');
    expect(container.textContent).toContain('发言权被其他用户占用');
    expect(mockButtons[0].onClick).toBe(onPreempt);
    expect(mockButtons[0].children).toContain('抢占发言权');
    expect(mockButtons[0].variant).toBe('ghost');
  });

  it('matches the user speech-mode strip metrics and semantic colors', () => {
    const { getByTestId, unmount } = render(
      <SpeakingLeaseControl status="unheld" onAcquire={jest.fn()} onPreempt={jest.fn()} onRelease={jest.fn()} />,
    );
    const root = getByTestId('speaking-lease-control');
    expect(root.className).toContain('mx-auto');
    expect(root.className).toContain('h-[26px]');
    expect(root.className).toContain('w-[calc(100%-64px)]');
    expect(root.className).toContain('bg-muted-foreground/10');
    expect(root.className).toContain('rounded-t-xl');
    unmount();
  });

  it('shows the held lease countdown and exposes release', () => {
    const onRelease = jest.fn();
    const { container } = render(
      <SpeakingLeaseControl
        status="held"
        lease={heldLease}
        nowMs={100_000}
        onAcquire={jest.fn()}
        onPreempt={jest.fn()}
        onRelease={onRelease}
      />,
    );
    expect(container.textContent).toContain('caller模式');
    expect(container.textContent).toContain('已获取caller身份发言');
    expect(container.textContent).toContain('剩余 80 秒');
    expect(mockButtons[0].onClick).toBe(onRelease);
    expect(mockButtons[0].children).toContain('释放');
  });
});

describe('SpeakingLeaseStrip', () => {
  beforeEach(() => {
    mockButtons.length = 0;
  });

  it('renders nothing unless speakingMode is lease_required', () => {
    const { container } = render(<SpeakingLeaseStrip />);
    expect(container.textContent).toBe('');
    const { container: withoutModel } = render(<SpeakingLeaseStrip model={undefined} />);
    expect(withoutModel.textContent).toBe('');
  });

  it('wires the model through to the control', () => {
    const model: GroupSpeakingLeaseModel = {
      speakingMode: 'lease_required',
      speakingLease: heldLease,
      speakingLeaseStatus: 'held',
      isUpdatingSpeakingLease: false,
      leaseNowMs: 100_000,
      acquireSpeakingLease: jest.fn(),
      releaseSpeakingLease: jest.fn(),
      refreshSpeakingLease: jest.fn(),
      composeSender: jest.fn(),
    };
    const { container } = render(<SpeakingLeaseStrip model={model} />);
    expect(container.textContent).toContain('caller模式');
    expect(container.textContent).toContain('已获取caller身份发言');
    expect(container.textContent).toContain('剩余 80 秒');
    expect(mockButtons[0].children).toContain('释放');
  });
});
