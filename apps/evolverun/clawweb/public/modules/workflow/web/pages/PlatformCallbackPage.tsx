import HttpCallbackPanel, { PLATFORM_WORKFLOW_ID } from '@avernet/workflow/web/components/HttpCallbackPanel'

/**
 * 平台级 HTTP 回调配置页面（仅管理员可见）。
 *
 * 设计要点：
 * - 复用 `http_callback_configs` 表，通过保留值 `__platform__` 作为 `workflow_id`。
 * - 复用现有的 `HttpCallbackPanel` 组件，传入 `isPlatform=true` 切换文案。
 * - 工作流级配置会覆盖平台级同名事件配置（详见 ClawMind 引擎调度逻辑）。
 */
export default function PlatformCallbackPage() {
  return (
    <div className="flex h-[calc(100vh-49px)] flex-col">
      <div className="border-b border-gray-200 bg-white px-6 py-4">
        <h1 className="text-xl font-semibold text-gray-900">平台级 HTTP 回调</h1>
        <p className="mt-1 text-sm text-gray-500">
          配置对所有工作流默认生效的 HTTP 回调通知。工作流级同名事件配置会覆盖平台级配置。
        </p>
      </div>
      <div className="mx-auto w-full max-w-5xl flex-1 overflow-y-auto px-6 py-4">
        <HttpCallbackPanel workflowId={PLATFORM_WORKFLOW_ID} isPlatform />
      </div>
    </div>
  )
}