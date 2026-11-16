import type { TodoArtifact, TodoEvent, TodoRecord } from '@/domain/todos/models';
import { create } from 'zustand';

export interface TodoStoreState {
  todos: TodoRecord[];
  total: number;
  selectedTodo: TodoRecord | null;
  interactions: TodoEvent[];
  events: TodoEvent[];
  artifacts: TodoArtifact[];
  listLoading: boolean;
  detailLoading: boolean;
  eventsLoading: boolean;
  artifactsLoading: boolean;
  mutationLoading: boolean;
  listError: string | null;
  detailError: string | null;
  eventsError: string | null;
  artifactsError: string | null;
  setTodos: (todos: TodoRecord[], total: number) => void;
  setSelectedTodo: (todo: TodoRecord | null) => void;
  setInteractions: (items: TodoEvent[]) => void;
  setEvents: (items: TodoEvent[]) => void;
  setArtifacts: (items: TodoArtifact[]) => void;
  setListLoading: (loading: boolean) => void;
  setDetailLoading: (loading: boolean) => void;
  setEventsLoading: (loading: boolean) => void;
  setArtifactsLoading: (loading: boolean) => void;
  setMutationLoading: (loading: boolean) => void;
  setListError: (error: string | null) => void;
  setDetailError: (error: string | null) => void;
  setEventsError: (error: string | null) => void;
  setArtifactsError: (error: string | null) => void;
  resetDetail: () => void;
}

const initialState = {
  todos: [],
  total: 0,
  selectedTodo: null,
  interactions: [],
  events: [],
  artifacts: [],
  listLoading: false,
  detailLoading: false,
  eventsLoading: false,
  artifactsLoading: false,
  mutationLoading: false,
  listError: null,
  detailError: null,
  eventsError: null,
  artifactsError: null,
};

export const useTodoStore = create<TodoStoreState>((set) => ({
  ...initialState,
  setTodos: (todos, total) => set({ todos, total, listError: null }),
  setSelectedTodo: (selectedTodo) => set({ selectedTodo }),
  setInteractions: (interactions) => set({ interactions }),
  setEvents: (events) => set({ events }),
  setArtifacts: (artifacts) => set({ artifacts }),
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
      detailLoading: false,
      eventsLoading: false,
      artifactsLoading: false,
      detailError: null,
      eventsError: null,
      artifactsError: null,
    }),
}));
