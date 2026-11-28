import type {
  TodoArtifact,
  TodoEvent,
  TodoProgress,
  TodoProductInteractionPage,
  TodoRecord,
} from '@/domain/todos/models';
import { create } from 'zustand';

export interface TodoStoreState {
  todos: TodoRecord[];
  total: number;
  selectedTodo: TodoRecord | null;
  interactions: TodoEvent[];
  events: TodoEvent[];
  artifacts: TodoArtifact[];
  progress: TodoProgress | null;
  productInteractionPage: TodoProductInteractionPage | null;
  listLoading: boolean;
  detailLoading: boolean;
  eventsLoading: boolean;
  artifactsLoading: boolean;
  progressLoading: boolean;
  productInteractionLoading: boolean;
  mutationLoading: boolean;
  listError: string | null;
  detailError: string | null;
  eventsError: string | null;
  artifactsError: string | null;
  progressError: string | null;
  productInteractionError: string | null;
  setTodos: (todos: TodoRecord[], total: number) => void;
  setSelectedTodo: (todo: TodoRecord | null) => void;
  setInteractions: (items: TodoEvent[]) => void;
  setEvents: (items: TodoEvent[]) => void;
  setArtifacts: (items: TodoArtifact[]) => void;
  setProgress: (progress: TodoProgress | null) => void;
  setProductInteractionPage: (page: TodoProductInteractionPage | null) => void;
  setListLoading: (loading: boolean) => void;
  setDetailLoading: (loading: boolean) => void;
  setEventsLoading: (loading: boolean) => void;
  setArtifactsLoading: (loading: boolean) => void;
  setProgressLoading: (loading: boolean) => void;
  setMutationLoading: (loading: boolean) => void;
  setListError: (error: string | null) => void;
  setDetailError: (error: string | null) => void;
  setEventsError: (error: string | null) => void;
  setArtifactsError: (error: string | null) => void;
  setProgressError: (error: string | null) => void;
  setProductInteractionLoading: (loading: boolean) => void;
  setProductInteractionError: (error: string | null) => void;
  resetDetail: () => void;
}

const initialState = {
  todos: [],
  total: 0,
  selectedTodo: null,
  interactions: [],
  events: [],
  artifacts: [],
  progress: null,
  productInteractionPage: null,
  listLoading: false,
  detailLoading: false,
  eventsLoading: false,
  artifactsLoading: false,
  progressLoading: false,
  productInteractionLoading: false,
  mutationLoading: false,
  listError: null,
  detailError: null,
  eventsError: null,
  artifactsError: null,
  progressError: null,
  productInteractionError: null,
};

export const useTodoStore = create<TodoStoreState>((set) => ({
  ...initialState,
  setTodos: (todos, total) => set({ todos, total, listError: null }),
  setSelectedTodo: (selectedTodo) => set({ selectedTodo }),
  setInteractions: (interactions) => set({ interactions }),
  setEvents: (events) => set({ events }),
  setArtifacts: (artifacts) => set({ artifacts }),
  setProgress: (progress) => set({ progress }),
  setProductInteractionPage: (productInteractionPage) => set({ productInteractionPage }),
  setProgressLoading: (progressLoading) => set({ progressLoading }),
  setProgressError: (progressError) => set({ progressError }),
  setProductInteractionLoading: (productInteractionLoading) => set({ productInteractionLoading }),
  setProductInteractionError: (productInteractionError) => set({ productInteractionError }),
  setListLoading: (listLoading) => set({ listLoading }),
  setDetailLoading: (detailLoading) => set({ detailLoading }),
  setEventsLoading: (eventsLoading) => set({ eventsLoading }),
  setArtifactsLoading: (artifactsLoading) => set({ artifactsLoading }),
  setMutationLoading: (mutationLoading) => set({ mutationLoading }),
  setListError: (listError) => set({ listError }),
  setDetailError: (detailError) => set({ detailError }),
  setEventsError: (eventsError) => set({ eventsError }),
  setArtifactsError: (artifactsError) => set({ artifactsError }),
  resetDetail: () =>
    set({
      selectedTodo: null,
      interactions: [],
      events: [],
      artifacts: [],
      progress: null,
      productInteractionPage: null,
      detailLoading: false,
      eventsLoading: false,
      artifactsLoading: false,
      progressLoading: false,
      productInteractionLoading: false,
      detailError: null,
      eventsError: null,
      artifactsError: null,
      progressError: null,
      productInteractionError: null,
    }),
}));
