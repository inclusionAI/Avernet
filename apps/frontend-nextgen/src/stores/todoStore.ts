import type { TodoArtifact, TodoEvent, TodoProgress, TodoRecord } from '@/domain/todos/models';
import { create } from 'zustand';

export interface TodoStoreState {
  todos: TodoRecord[];
  total: number;
  selectedTodo: TodoRecord | null;
  interactions: TodoEvent[];
  events: TodoEvent[];
  artifacts: TodoArtifact[];
  progress: TodoProgress | null;
  listLoading: boolean;
  detailLoading: boolean;
  eventsLoading: boolean;
  artifactsLoading: boolean;
  progressLoading: boolean;
  mutationLoading: boolean;
  listError: string | null;
  detailError: string | null;
  eventsError: string | null;
  artifactsError: string | null;
  progressError: string | null;
  setTodos: (todos: TodoRecord[], total: number) => void;
  setSelectedTodo: (todo: TodoRecord | null) => void;
  setInteractions: (items: TodoEvent[]) => void;
  setEvents: (items: TodoEvent[]) => void;
  setArtifacts: (items: TodoArtifact[]) => void;
  setProgress: (progress: TodoProgress | null) => void;
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
  listLoading: false,
  detailLoading: false,
  eventsLoading: false,
  artifactsLoading: false,
  progressLoading: false,
  mutationLoading: false,
  listError: null,
  detailError: null,
  eventsError: null,
  artifactsError: null,
  progressError: null,
};

export const useTodoStore = create<TodoStoreState>((set) => ({
  ...initialState,
  setTodos: (todos, total) => set({ todos, total, listError: null }),
  setSelectedTodo: (selectedTodo) => set({ selectedTodo }),
  setInteractions: (interactions) => set({ interactions }),
  setEvents: (events) => set({ events }),
  setArtifacts: (artifacts) => set({ artifacts }),
  setProgress: (progress) => set({ progress }),
  setProgressLoading: (progressLoading) => set({ progressLoading }),
  setProgressError: (progressError) => set({ progressError }),
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
      detailLoading: false,
      eventsLoading: false,
      artifactsLoading: false,
      progressLoading: false,
      detailError: null,
      eventsError: null,
      artifactsError: null,
      progressError: null,
    }),
}));
