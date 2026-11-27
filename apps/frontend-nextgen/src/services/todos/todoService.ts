import type { BotDomain } from '@/domain/botWorkshop';
import type { TodoBotOption, TodoOwner } from '@/domain/todos/models';
import { mapBotList } from '@/services/botWorkshop/botMapper';
import {
  listTodoBotInventory,
  listTodoSpaceMembers,
  TODO_ENDPOINTS,
} from '@/services/backendApi/todos/todoController';
import { isEnvelopeSuccess } from '@/services/backendApi/types';
import type { BackendApiEnvelope } from '@/services/backendApi/types';

/**
 * Todo domain service facade.
 *
 * Hooks and components consume this module instead of importing the backend API
 * implementation directly, preserving Component → Hook → Service → Controller.
 */
export {
  acceptTodo,
  addBotTeam,
  addTodo,
  addTodoInteraction,
  addTodoProductInteraction,
  archiveTodoBotTeam,
  createTodoAutoImportRule,
  downloadTodoArtifactContent,
  importTodoDimaItems,
  loadBotTeam,
  loadBotTeams,
  loadTodo,
  loadTodoProgress,
  loadTodoArtifacts,
  loadTodoAutoImportRules,
  loadTodoEvents,
  loadTodoInteractions,
  loadTodos,
  loadTodoSpaces,
  loadTodoSourceDraft,
  loadTodoSourceProjects,
  loadTodoSourceWorkItems,
  loadTodoProductInteractionPage,
  saveBotTeam,
  startTodoRun,
  stopTodoProductInteraction,
  updateTodoAutoImportRule,
} from '@/services/backendApi/todos/todoService';
export type { BotTeamPage, TodoPage, TodoStatus } from '@/services/backendApi/todos/todoService';

function requireSuccess<T>(response: BackendApiEnvelope<T>, path: string): T {
  if (!isEnvelopeSuccess(response) || response.data === undefined || response.data === null) {
    throw new Error(response.message || `请求失败: ${path}`);
  }
  return response.data;
}

/** Load Bot inventory through the narrow authenticated Todo product facade. */
export async function loadTodoBots(spaceId: number | string): Promise<BotDomain[]> {
  const response = await listTodoBotInventory(spaceId);
  const page = requireSuccess(response, TODO_ENDPOINTS.bots);
  return mapBotList(page).items;
}

/** Load all selectable owners through the Todo Space-member facade. */
export async function loadTodoOwners(spaceId: number | string): Promise<TodoOwner[]> {
  const owners: TodoOwner[] = [];
  for (let page = 1; ; page += 1) {
    const response = await listTodoSpaceMembers(spaceId, page);
    const data = requireSuccess(response, TODO_ENDPOINTS.spaceMembers(spaceId));
    const items = data.items ?? [];
    owners.push(
      ...items.flatMap((raw) => {
        const userId = typeof raw.user_id === 'string' ? raw.user_id.trim() : '';
        if (!userId) return [];
        const displayName =
          typeof raw.display_name === 'string' && raw.display_name.trim()
            ? raw.display_name.trim()
            : typeof raw.user_name === 'string' && raw.user_name.trim()
              ? raw.user_name.trim()
              : userId;
        return [{ userId, userName: displayName }];
      }),
    );
    if (items.length < 100 || owners.length >= (data.total ?? 0)) break;
  }
  return owners;
}

/** Mention candidates follow the Todo's own Space, not the currently selected UI Space. */
export async function loadTodoMentionBots(spaceId: number): Promise<TodoBotOption[]> {
  const bots = await loadTodoBots(spaceId);
  const seen = new Set<string>();
  return bots.flatMap((bot) => {
    if (!bot.id || seen.has(bot.id)) return [];
    seen.add(bot.id);
    return [{ botId: bot.id, botName: bot.name || bot.id }];
  });
}
