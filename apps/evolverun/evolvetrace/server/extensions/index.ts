/**
 * EvolveTrace extensions module.
 *
 * 定义企业扩展点接口，遵循与 TaskguardExtensions 相同的设计模式。
 */
export type {
  EvolvetraceExtensions,
  EvolvetraceServerConfig,
  FlowRunRepositoryLike,
  NodeExecutionRepositoryLike,
  FlowEventRepositoryLike,
  WorkflowSpecRepositoryLike,
  FacadeBindingRepositoryLike,
  BotWorkflowPermissionRepositoryLike,
  MetricsRepositoryLike,
  AlertRepositoryLike,
  FlowControlRepositoryLike,
  ExecutionStepLogRepositoryLike,
  NotificationConfigRepositoryLike,
  DeployHistoryRepositoryLike,
  HttpCallbackConfigRepositoryLike,
} from "./types.js";