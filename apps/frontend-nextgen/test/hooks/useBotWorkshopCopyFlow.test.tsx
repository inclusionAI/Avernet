/** @jest-environment jsdom */
import { act, renderHook } from '@testing-library/react';
import { useBotWorkshopCopyFlow } from '@/hooks/useBotWorkshopCopyFlow';
import { botWorkshopService } from '@/services/botWorkshop';
import type { BotCopyInput } from '@/services/botWorkshop';
import { botAvatarService } from '@/services/botWorkshop/botAvatarService';
import { toast } from 'sonner';

jest.mock('@/services/botWorkshop', () => ({
  botWorkshopService: { copyBot: jest.fn(), detail: jest.fn() },
  BackendRequestError: class extends Error {},
}));
jest.mock('@/services/botWorkshop/botAvatarService', () => ({ botAvatarService: { save: jest.fn() } }));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), warning: jest.fn(), error: jest.fn() } }));

const input: BotCopyInput = { sourceBotId: 'src', name: 'copy', description: '', ownership: 'personal', serviceMode: 'non-service', excludes: [], avatarUrl: 'data:avatar' };

describe('copy avatar completion', () => {
  beforeEach(() => { jest.useFakeTimers(); jest.clearAllMocks(); });
  afterEach(() => { jest.useRealTimers(); });

  it('waits for the destination and reports avatar save failure without repeating the copy', async () => {
    (botWorkshopService.copyBot as jest.Mock).mockResolvedValue({ type: 'issued', botId: 'dst', operationId: 'op' });
    (botWorkshopService.detail as jest.Mock).mockResolvedValue({ id: 'dst', name: 'copy' });
    (botAvatarService.save as jest.Mock).mockRejectedValue(new Error('write failed'));
    const close = jest.fn();
    const load = jest.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useBotWorkshopCopyFlow({ load, onClose: close }));
    await act(async () => { await result.current.submitCopy(input); });
    expect(botAvatarService.save).not.toHaveBeenCalled();
    await act(async () => { await jest.advanceTimersByTimeAsync(5000); });
    expect(botAvatarService.save).toHaveBeenCalledWith('dst', 'data:avatar');
    expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining('头像未保存'));
    expect(botWorkshopService.copyBot).toHaveBeenCalledTimes(1);
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('keeps the pending write across parent rerenders', async () => {
    (botWorkshopService.copyBot as jest.Mock).mockResolvedValue({ type: 'issued', botId: 'dst', operationId: 'op' });
    (botWorkshopService.detail as jest.Mock).mockResolvedValue({ id: 'dst', name: 'copy' });
    let rejectSave!: (reason: Error) => void;
    (botAvatarService.save as jest.Mock).mockReturnValue(new Promise((_resolve, reject) => { rejectSave = reject; }));
    const load = jest.fn().mockResolvedValue(undefined);
    const { result, rerender } = renderHook(() => useBotWorkshopCopyFlow({ load, onClose: () => {} }));
    await act(async () => { await result.current.submitCopy(input); });
    await act(async () => { await jest.advanceTimersByTimeAsync(5000); });
    rerender();
    await act(async () => { rejectSave(new Error('write failed')); });
    expect(botAvatarService.save).toHaveBeenCalledTimes(1);
    expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining('头像未保存'));
  });
});
