import type { TodoEvent, TodoRecord } from '@/domain/todos/models';
import { create } from 'zustand';

export interface TodoStoreState {
  todos: TodoRecord[];
  total: number;
  selectedTodo: TodoRecord | null;
  interactions: TodoEvent[];
  events: TodoEvent[];
  listLoading: boolean;
  detailLoading: boolean;
  eventsLoading: boolean;
  mutationLoading: boolean;
  listError: string | null;
  detailError: string | null;
  eventsError: string | null;
  setTodos: (todos: TodoRecord[], total: number) => void;
  setSelectedTodo: (todo: TodoRecord | null) => void;
  setInteractions: (items: TodoEvent[]) => void;
  setEvents: (items: TodoEvent[]) => void;
  setListLoading: (loading: boolean) => void;
  setDetailLoading: (loading: boolean) => void;
  setEventsLoading: (loading: boolean) => void;
  setMutationLoading: (loading: boolean) => void;
  setListError: (error: string | null) => void;
  setDetailError: (error: string | null) => void;
  setEventsError: (error: string | null) => void;
  resetDetail: () => void;
}

const initialState = {
  todos: [],
  total: 0,
  selectedTodo: null,
  interactions: [],
  events: [],
  listLoading: false,
  detailLoading: false,
  eventsLoading: false,
  mutationLoading: false,
  listError: null,
  detailError: null,
  eventsError: null,
};

export const useTodoStore = create<TodoStoreState>((set) => ({
  ...initialState,
  setTodos: (todos, total) => set({ todos, total, listError: null }),
  setSelectedTodo: (selectedTodo) => set({ selectedTodo }),
  setInteractions: (interactions) => set({ interactions }),
  setEvents: (events) => set({ events }),
  setListLoading: (listLoading) => set({ listLoading }),
  setDetailLoading: (detailLoading) => set({ detailLoading }),
  setEventsLoading: (eventsLoading) => set({ eventsLoading }),
  setMutationLoading: (mutationLoading) => set({ mutationLoading }),
  setListError: (listError) => set({ listError }),
  setDetailError: (detailError) => set({ detailError }),
  setEventsError: (eventsError) => set({ eventsError }),
  resetDetail: () =>
    set({
      selectedTodo: null,
      interactions: [],
      events: [],
      detailLoading: false,
      eventsLoading: false,
      detailError: null,
      eventsError: null,
    }),
}));
