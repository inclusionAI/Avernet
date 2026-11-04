import { decodeOpenCreateGroupOptions, type OpenCreateGroupOptions } from '@/domain/collaborationDraft';
import { create } from 'zustand';

export interface CollaborationGroupLaunchRequest {
  id: number;
  source: string;
  options: OpenCreateGroupOptions;
}

interface CollaborationGroupLaunchState {
  request: CollaborationGroupLaunchRequest | null;
  busy: boolean;
  sequence: number;
  open: (source: string, options?: unknown) => void;
  close: (source?: string) => void;
  setBusy: (busy: boolean) => void;
}

export const useCollaborationGroupLaunchStore = create<CollaborationGroupLaunchState>((set, get) => ({
  request: null,
  busy: false,
  sequence: 0,
  open(source, input) {
    const options = decodeOpenCreateGroupOptions(input);
    const { request, busy, sequence } = get();
    if (busy || (request?.source === source && JSON.stringify(request.options) === JSON.stringify(options))) return;
    set({ request: { id: sequence + 1, source, options }, sequence: sequence + 1 });
  },
  close(source) {
    if (!get().busy && (!source || get().request?.source === source)) set({ request: null });
  },
  setBusy: (busy) => set({ busy }),
}));
