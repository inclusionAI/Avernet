import type { TodoBotOption } from '@/domain/todos/models';
import { botWorkshopService } from '@/services/botWorkshop/botWorkshopService';

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
  downloadTodoArtifactContent,
  loadTodo,
  loadTodoArtifacts,
  loadTodoEvents,
  loadTodoInteractions,
  loadTodos,
  loadTodoSourceDraft,
  loadTodoSourceProjects,
  loadTodoSourceWorkItems,
  startTodoRun,
} from '@/services/backendApi/todos/todoService';
export type { TodoPage, TodoStatus } from '@/services/backendApi/todos/todoService';

/** Mention candidates follow the Todo's own Space, not the currently selected UI Space. */
export async function loadTodoMentionBots(spaceId: number): Promise<TodoBotOption[]> {
  const result = await botWorkshopService.list({
    spaceId: String(spaceId),
    page: 1,
    pageSize: 100,
  });
  const seen = new Set<string>();
  return result.items.flatMap((bot) => {
    if (!bot.id || seen.has(bot.id)) return [];
    seen.add(bot.id);
    return [{ botId: bot.id, botName: bot.name || bot.id }];
  });
}
