import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { managementEvents, managementTime } from '@/domain/taskEscortParity/management';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortManagementResource } from '@/hooks/taskEscortParity/useTaskEscortManagement';
import { managementService } from '@/services/taskEscortParity/managementService';
import { useCallback, useId } from 'react';
import { DisabledWrite, Fields, ReadState, yesNo } from './ManagementPrimitives';

function SecretField({ label, configured }: { label: string; configured: boolean }) {
  const id = useId();
  return (
    <div className="space-y-2">
      <label htmlFor={id}>{label}</label>
      <Input
        id={id}
        aria-label={label}
        type="password"
        value=""
        readOnly
        disabled
        autoComplete="off"
        placeholder={configured ? '已配置（不读取凭证）' : '未配置'}
      />
      <p className="text-muted-foreground">
        {configured ? '服务端凭证已隐藏；掩码不可作为密钥复用。' : '服务端未配置密钥。'}
      </p>
    </div>
  );
}
export function ManagementNotification({ context }: { context: TaskEscortParityContext }) {
  const read = useCallback(
    (signal: AbortSignal) => managementService.notification(context.workflowId, context.userId!, signal),
    [context.workflowId, context.userId],
  );
  const resource = useTaskEscortManagementResource(context, 'notification', read);
  const config = resource.data;
  return (
    <section aria-label="钉钉工作流通知配置" className="space-y-4">
      <h3 className="text-sm font-medium">钉钉工作流通知配置</h3>
      <ReadState resource={resource} />
      {!resource.loading && !resource.error && config === null && <Empty compact title="尚未配置工作流通知" />}
      {config && (
        <>
          <Fields
            fields={[
              ['robotCode', config.robotCode],
              ['默认失败通知标题', config.onFailureMessageTitle],
              ['包含运行链接', yesNo(config.onFailureMessageIncludeRunLink)],
            ]}
          />
          <SecretField label="appSecret" configured={config.hasSecret} />
          <div className="space-y-2">
            <h4 className="font-medium">订阅事件</h4>
            <p>
              {config.notifyOn === null
                ? '未显式配置（使用服务端旧版事件策略）'
                : config.notifyOn.length
                ? config.notifyOn.map((e) => managementEvents[e] ?? e).join('、')
                : '未订阅任何事件'}
            </p>
          </div>
          <section aria-label="事件消息配置" className="space-y-3">
            <h4 className="font-medium">事件消息配置</h4>
            <div className="space-y-3">
              {Object.entries(config.eventOptions).map(([event, options]) => (
                <section
                  key={event}
                  aria-label={`${managementEvents[event] ?? event}消息配置`}
                  className="min-w-0 space-y-3 rounded-lg border border-border bg-muted/20 p-4"
                >
                  <h5 className="font-medium">{managementEvents[event] ?? event}</h5>
                  <Fields
                    fields={[
                      ['标题', options.title],
                      ['包含运行链接', yesNo(options.includeRunLink)],
                      ['包含节点输出', yesNo(options.includeNodeOutput)],
                      ['包含最终总结输出', yesNo(options.includeFinalOutput)],
                      ['最大输出字段数', options.maxOutputFields],
                      ['字段值长度上限', options.maxOutputValueLen],
                    ]}
                  />
                </section>
              ))}
              {!Object.keys(config.eventOptions).length && <p className="text-muted-foreground">未配置事件级覆盖</p>}
            </div>
          </section>
          <Fields
            fields={[
              [
                '单聊通知人',
                config.onFailureUsers.length ? (
                  <ul>
                    {config.onFailureUsers.map((u) => (
                      <li key={u.userId}>
                        {u.name ? `${u.name} · ` : ''}
                        {u.userId}
                      </li>
                    ))}
                  </ul>
                ) : (
                  '暂无通知人'
                ),
              ],
              [
                '群聊通知群',
                config.onFailureGroups.length ? (
                  <ul>
                    {config.onFailureGroups.map((g) => (
                      <li key={g.openConversationId}>
                        {g.name ? `${g.name} · ` : ''}
                        {g.openConversationId}
                      </li>
                    ))}
                  </ul>
                ) : (
                  '暂无通知群'
                ),
              ],
            ]}
          />
        </>
      )}
      <div className="flex flex-wrap gap-3">
        <DisabledWrite>保存通知配置</DisabledWrite>
        {config && <DisabledWrite destructive>删除通知配置</DisabledWrite>}
      </div>
    </section>
  );
}
export function ManagementCallbacks({ context }: { context: TaskEscortParityContext }) {
  const read = useCallback(
    (signal: AbortSignal) => managementService.callbacks(context.workflowId, context.userId!, signal),
    [context.workflowId, context.userId],
  );
  const resource = useTaskEscortManagementResource(context, 'callbacks', read);
  return (
    <section aria-label="HTTP 回调配置" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-medium">HTTP 回调配置</h3>
        <DisabledWrite>添加回调</DisabledWrite>
      </div>
      <ReadState resource={resource} />
      {resource.data?.length === 0 && <Empty compact title="暂无 HTTP 回调配置" />}
      <ul className="space-y-3">
        {resource.data?.map((c) => (
          <li key={c.configId} className="space-y-3 rounded-lg border border-border p-3">
            <Fields
              fields={[
                ['名称', c.name],
                ['启用', yesNo(c.enabled)],
                ['回调地址', c.url],
                ['订阅事件', c.notifyOn.map((e) => managementEvents[e] ?? e).join('、') || '无'],
              ]}
            />
            <details>
              <summary className="cursor-pointer text-primary">回调详情</summary>
              <div className="mt-3 space-y-4">
                <Fields
                  fields={[
                    ['配置 ID', c.configId],
                    ['工作流', c.workflowId],
                    ['超时', `${c.timeoutMs} ms`],
                    ['最大重试次数', c.maxRetries],
                    ['重试间隔', `${c.retryDelayMs} ms`],
                    ['包含节点输出', yesNo(c.includeNodeOutput)],
                    ['创建时间', managementTime(c.createdAtMs)],
                    ['更新时间', managementTime(c.modifiedAtMs)],
                  ]}
                />
                <SecretField label="回调签名密钥" configured={c.hasSecret} />
                <p className="text-muted-foreground">仅展示配置；不会调用回调地址或发送测试事件。</p>
                <div className="flex flex-wrap gap-3">
                  <DisabledWrite>保存回调</DisabledWrite>
                  <DisabledWrite destructive>删除回调</DisabledWrite>
                </div>
              </div>
            </details>
          </li>
        ))}
      </ul>
    </section>
  );
}
