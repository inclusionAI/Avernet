/**
 * 任务执行 Loop 服务层类型。领域模型下沉 src/domain/tasks/models；
 * 本文件只保留请求 DTO（ExecuteTaskRequest）与前端写死的 workflow 列表。
 */
import type { TaskExecConfig, TaskSourceType } from '@/domain/tasks/models';

export type {
  Envelope,
  TaskExecConfig,
  TaskInfo,
  TaskListItem,
  TaskRecord,
  TaskSourceType,
  TaskStatus,
} from '@/domain/tasks/models';

export interface ExecuteTaskRequest {
  task_spec: {
    metadata: { title: string; instruction: string };
    context: {
      background: string;
      extend_props: {
        teamclaw_context?: {
          main_session_id?: string;
          main_session_name?: string;
          source_group_id?: string;
          parent_task_id?: string | null;
        };
        [key: string]: unknown;
      };
    };
    goal: { objective: string; acceptances: Array<{ id: string; acceptance: string }> };
  };
  source_type: TaskSourceType;
  owner_user_id: string;
  owner_bot_id: string;
  execution_config: TaskExecConfig;
}

/** execute 响应（后端精简为 4 字段）。 */
export interface ExecuteTaskResponse {
  task_id: string;
  success: boolean;
  run_id: number;
  message: string | null;
  /** 扩展属性；建群任务成功后回带的协作群 ID 置于 extend_props.group_id（后端 execute 同学稍后同步回带，先占位）。 */
  extend_props?: {
    group_id?: string;
    [key: string]: unknown;
  };
}
