// 完整发现适配器：保留旧导入面，任务实现不进入 collab 的公开发现闭包。
import { mapBbsTaskItemDto, mapPlazaStatusToBbsStatus } from '@/domain/collaborationSquare/taskMapper';
import type { PublicTask, PublicTaskPage, PublicTaskSearchQuery } from '@/domain/collaborationSquare/types';
import { listBbsTasks } from '@/services/backendApi/collaboration/bbsTaskController';
import { isAceLoginResponse, mapListError, resolveBotNames, unsupported } from './collaborationSquareApiHelpers';
import { CollaborationSquareError } from './collaborationSquareError';
import type { CollaborationSquareGateway } from './collaborationSquareGateway';
import { PublicDiscoveryApiAdapter } from './publicDiscoveryApiAdapter';

export class CollaborationSquareApiAdapter extends PublicDiscoveryApiAdapter implements CollaborationSquareGateway {
  // 任务广场：接入真实 BBS 接力公开任务端点 GET /openapi/v1/collaboration/tasks/bbs/list（1-based 分页 +
  // 可选 status / search_word 过滤，`total` 为过滤后行数）。分页 / 过滤 / 排序均在服务端，adapter 只做参数
  // 换算与展示名反查：offset/limit→page/page_size、search→search_word、广场态 status→BBS 原始态；publisher 与
  // assignee 经 bots/query 反查为展示名（复合 bot_id:owner 由 resolveBotNames 拆 realBotId 下发，按原始 id 回填）。
  async listPublicTasks(query: PublicTaskSearchQuery = {}, signal?: AbortSignal): Promise<PublicTaskPage> {
    try {
      const limit = typeof query.limit === 'number' && query.limit > 0 ? query.limit : undefined;
      const page = limit && typeof query.offset === 'number' ? Math.floor(query.offset / limit) + 1 : undefined;
      const searchWord = query.search?.trim() || undefined;
      const statusParam = query.status && query.status !== 'all' ? mapPlazaStatusToBbsStatus(query.status) : undefined;
      const resp = await listBbsTasks(
        {
          ...(page ? { page } : {}),
          ...(limit ? { page_size: limit } : {}),
          ...(searchWord ? { search_word: searchWord } : {}),
          ...(statusParam ? { status: statusParam } : {}),
        },
        signal,
      );
      if (isAceLoginResponse(resp)) {
        throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
      }
      if (resp.code !== 200000) {
        throw new CollaborationSquareError('protocol_error', '任务广场接口返回了无法识别的业务码');
      }
      const items = resp.data?.items ?? [];
      // 收集非空 publisher Bot ID，经 bots/query 批量反查展示名（与群主 driver_bot_uuid 反查同法）；
      // 查到的用名、查不到的兜底用 ID（由 mapBbsTaskItemDto 完成），publisher 为 null 不参与反查。
      const publisherIds = [
        ...new Set(
          items.map((item) => item.publisher).filter((id): id is string => typeof id === 'string' && id.trim() !== ''),
        ),
      ];
      const publisherNameMap = publisherIds.length > 0 ? await resolveBotNames(publisherIds) : {};
      // 认领者同理：收集已认领任务的 assignee_id 反查展示名。后端未给 assignee_name 时用反查名兜底，
      // 反查也查不到再兜底 assignee_id（由 mapBbsTaskItemDto 完成）。复合 bot_id:owner 由 resolveBotNames 拆 realBotId。
      const assigneeIds = [
        ...new Set(
          items
            .map((item) => item.assignee_id)
            .filter((id): id is string => typeof id === 'string' && id.trim() !== ''),
        ),
      ];
      const assigneeNameMap = assigneeIds.length > 0 ? await resolveBotNames(assigneeIds) : {};
      const mapped = items.flatMap((dto) => {
        const task = mapBbsTaskItemDto(dto, publisherNameMap, assigneeNameMap);
        return task ? [task] : [];
      });
      return { items: mapped, total: resp.data?.total ?? mapped.length };
    } catch (error) {
      return mapListError(error, '任务广场');
    }
  }

  // 任务详情：真实 BBS 端点不提供单点详情，Hook 改用已加载列表项填充详情；接口保留显式 unsupported，
  // 避免调用方误以为可取详情而发请求。参数对齐 Gateway 声明但不使用。
  async getPublicTask(_taskId: string, _signal?: AbortSignal): Promise<PublicTask> {
    void _taskId;
    void _signal;
    return unsupported('任务详情改用列表项填充，真实 BBS 端点不提供单点详情');
  }
}

export const collaborationSquareApiAdapter = new CollaborationSquareApiAdapter();
