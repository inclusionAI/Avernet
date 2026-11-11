/**
 * Todo domain service facade.
 *
 * Hooks and components consume this module instead of importing the backend API
 * implementation directly, preserving Component → Hook → Service → Controller.
 */
export {
  acceptTodo,
  addTodo,
  addTodoInteraction,
  loadTodo,
  loadTodoEvents,
  loadTodoInteractions,
  loadTodos,
  startTodoRun,
} from '@/services/backendApi/todos/todoService';
export type { TodoPage, TodoStatus } from '@/services/backendApi/todos/todoService';
