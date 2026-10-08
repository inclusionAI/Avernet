import type { BotCreateScenario, BotDeployment, BotDomain, BotServiceMode } from '@/domain/botWorkshop';
import { create } from 'zustand';

export interface BotWorkshopState {
  spaceId: string;
  keyword: string;
  engine: string;
  deployment?: BotDeployment;
  serviceMode?: BotServiceMode;
  page: number;
  pageSize: number;
  items: BotDomain[];
  total?: number;
  hasMore?: boolean;
  loading: boolean;
  error?: string;
  createScenario?: BotCreateScenario;
  setSpaceId: (value: string) => void;
  setKeyword: (value: string) => void;
  setEngine: (value: string) => void;
  setDeployment: (value?: BotDeployment) => void;
  setServiceMode: (value?: BotServiceMode) => void;
  setPage: (value: number) => void;
  setPageSize: (value: number) => void;
  setCreateScenario: (value?: BotCreateScenario) => void;
  setQueryState: (
    value: Partial<Pick<BotWorkshopState, 'spaceId' | 'keyword' | 'engine' | 'deployment' | 'serviceMode' | 'page'>>,
  ) => void;
  setResult: (value: Pick<BotWorkshopState, 'items' | 'total' | 'hasMore' | 'loading' | 'error'>) => void;
  reset: () => void;
}
const initial = {
  spaceId: '',
  keyword: '',
  engine: '',
  deployment: undefined,
  serviceMode: undefined,
  page: 1,
  pageSize: 20,
  items: [],
  total: undefined,
  hasMore: undefined,
  loading: false,
  error: undefined,
  createScenario: undefined,
};
export const useBotWorkshopStore = create<BotWorkshopState>((set) => ({
  ...initial,
  setSpaceId: (spaceId) => set({ spaceId }),
  setKeyword: (keyword) => set({ keyword, page: 1 }),
  setEngine: (engine) => set({ engine, page: 1 }),
  setDeployment: (deployment) => set({ deployment, page: 1 }),
  setServiceMode: (serviceMode) => set({ serviceMode, page: 1 }),
  setPage: (page) => set({ page }),
  setPageSize: (pageSize) => set({ pageSize, page: 1 }),
  setCreateScenario: (createScenario) => set({ createScenario }),
  setQueryState: (value) => set(value),
  setResult: (value) => set(value),
  reset: () => set(initial),
}));
