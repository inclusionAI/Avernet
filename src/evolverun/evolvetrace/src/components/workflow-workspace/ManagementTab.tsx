import { useState } from 'react'
import { useWorkflowHistory, useDeleteWorkflow, useRestoreWorkflowVersion, useFacadeBindings } from '../../api/hooks'
import { getClientUser } from '../../hooks/useClientUser'
import { formatTimeShort } from '../../utils/time'
import PermissionPanel from './PermissionPanel'
import FacadePanel from './FacadePanel'
import NotificationPanel from './NotificationPanel'
import HttpCallbackPanel from './HttpCallbackPanel'

function SectionCard({
  title,
  children,
  danger = false,
}: {
  title: string
  children: React.ReactNode
  danger?: boolean
}) {
  return (
    <div className={`rounded-lg border bg-white p-5 shadow-sm ${danger ? 'border-red-200' : 'border-gray-200'}`}>
      <h3 className={`text-sm font-semibold ${danger ? 'text-red-700' : 'text-gray-900'}`}>{title}</h3>
      <div className="mt-4">{children}</div>
    </div>
  )
}

type SettingsTab = 'permissions' | 'facades' | 'notification' | 'callback'

const SETTINGS_TABS: { key: SettingsTab; label: string }[] = [
  { key: 'permissions', label: '权限' },
  { key: 'facades', label: '命令' },
  { key: 'notification', label: '通知' },
  { key: 'callback', label: '回调' },
]

interface ManagementTabProps {
  workflowId: string
  workflowTitle: string | null
  onDeleted: () => void
}

export default function ManagementTab({ workflowId, workflowTitle, onDeleted }: ManagementTabProps) {
  const user = getClientUser()
  const isAdmin = user?.isAdmin === true

  const [settingsTab, setSettingsTab] = useState<SettingsTab>('permissions')

  const { data, isLoading, isError, error, refetch } = useWorkflowHistory(workflowId)
  const { data: allFacades, refetch: refetchFacades } = useFacadeBindings()
  const deleteMutation = useDeleteWorkflow()
  const restoreMutation = useRestoreWorkflowVersion()

  const history = data?.history ?? []
  const facades = (allFacades ?? []).filter((f) => f.workflowId === workflowId)

  const handleDelete = () => {
    const name = workflowTitle || workflowId
    if (!window.confirm(`确定删除工作流 "${name}" 吗？删除后无法恢复。`)) return
    deleteMutation.mutate(workflowId, {
      onSuccess: () => onDeleted(),
      onError: (err) => alert(`删除失败：${err instanceof Error ? err.message : String(err)}`),
    })
  }

  const handleRollback = (version: number, deployNumber: number) => {
    if (!window.confirm(`确定回滚到版本 ${version}（deploy #${deployNumber}）吗？这将保存为新的部署记录。`)) return
    restoreMutation.mutate(
      { workflowId, version },
      {
        onSuccess: () => alert('回滚成功，已生成新的部署记录'),
        onError: (err) => alert(`回滚失败：${err instanceof Error ? err.message : String(err)}`),
      },
    )
  }

  return (
    <div className="space-y-4">
      <SectionCard title="版本历史">
        {isError ? (
          <p className="text-sm text-red-600">
            加载失败：{error instanceof Error ? error.message : String(error)}
            <button onClick={() => void refetch()} className="ml-2 text-xs text-blue-600 hover:underline">
              重试
            </button>
          </p>
        ) : isLoading ? (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-200 border-t-blue-600" />
            加载版本历史中…
          </div>
        ) : history.length === 0 ? (
          <p className="text-sm text-gray-500">暂无版本历史</p>
        ) : (
          <div className="space-y-2">
            {history.map((item, index) => {
              const isCurrent = index === 0
              return (
                <div
                  key={item.deployNumber}
                  className={`flex items-center justify-between rounded-md border p-3 text-sm ${
                    isCurrent ? 'border-green-200 bg-green-50' : 'border-gray-200 bg-white'
                  }`}
                >
                  <div className="min-w-0 space-y-0.5">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-gray-900">
                        v{item.version} · deploy #{item.deployNumber}
                      </span>
                      {isCurrent && (
                        <span className="rounded-full bg-green-100 px-2 py-0.5 text-[10px] font-medium text-green-700">
                          当前
                        </span>
                      )}
                      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] text-gray-600">
                        {item.action}
                      </span>
                    </div>
                    <p className="truncate text-xs text-gray-500">
                      {item.note || '无备注'} · {formatTimeShort(item.gmtCreate)}
                    </p>
                  </div>
                  {!isCurrent && isAdmin && (
                    <button
                      onClick={() => handleRollback(item.version, item.deployNumber)}
                      disabled={restoreMutation.isPending}
                      className="ml-3 inline-flex items-center rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {restoreMutation.isPending ? '回滚中…' : '回滚'}
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </SectionCard>

      <SectionCard title="工作流设置">
        <div className="mb-4 flex items-center gap-1 border-b border-gray-200">
          {SETTINGS_TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setSettingsTab(tab.key)}
              className={`border-b-2 px-3 py-1.5 text-xs font-medium transition-colors ${
                settingsTab === tab.key
                  ? 'border-blue-600 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {tab.label}
              {tab.key === 'facades' && facades.length > 0 && (
                <span className="ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-purple-100 px-1 text-[10px] leading-none text-purple-700">
                  {facades.length}
                </span>
              )}
            </button>
          ))}
        </div>

        {settingsTab === 'permissions' && <PermissionPanel workflowId={workflowId} />}
        {settingsTab === 'facades' && (
          <FacadePanel
            workflowId={workflowId}
            facades={facades}
            onFacadeChanged={() => void refetchFacades()}
          />
        )}
        {settingsTab === 'notification' && <NotificationPanel workflowId={workflowId} />}
        {settingsTab === 'callback' && <HttpCallbackPanel workflowId={workflowId} />}
      </SectionCard>

      {isAdmin && (
        <SectionCard title="危险操作" danger>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-gray-900">删除工作流</p>
              <p className="text-xs text-gray-500">删除后无法恢复，请谨慎操作</p>
            </div>
            <button
              onClick={handleDelete}
              disabled={deleteMutation.isPending}
              className="inline-flex items-center rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {deleteMutation.isPending ? '删除中…' : '删除'}
            </button>
          </div>
        </SectionCard>
      )}
    </div>
  )
}
