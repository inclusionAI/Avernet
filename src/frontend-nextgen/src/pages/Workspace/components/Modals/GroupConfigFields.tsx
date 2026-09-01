import { Button, Checkbox, Input, Segmented } from '@/components/ui';
import { cn } from '@/utils/cn';
import type { ReactNode } from 'react';
import { GroupLeaderSelect, type GroupLeaderOption } from './GroupLeaderSelect';
import { YamlCodeEditor } from './YamlEditor';

export type GroupStrategyKind = 'free_chat' | 'task_master_slave' | 'task_dag';
export type DeliveryPolicy = 'send_to_driver' | 'inject_observers';

const STRATEGY_OPTIONS: Array<{ value: GroupStrategyKind; label: string; description: string }> = [
  { value: 'free_chat', label: '自由聊天', description: '适合开放交流、灵活讨论和日常协作' },
  {
    value: 'task_master_slave',
    label: '任务协作',
    description: '主从模式，适合按主节点统一推进、成员节点配合执行的任务协作',
  },
  { value: 'task_dag', label: '自定义协作', description: '状态机编排，支持以 YAML 定义协同流程' },
];

const DELIVERY_OPTIONS: Array<{ value: DeliveryPolicy; label: string }> = [
  { value: 'send_to_driver', label: '自动回复' },
  { value: 'inject_observers', label: '关闭自动回复' },
];

export interface GroupConfigFieldsProps {
  kind: GroupStrategyKind;
  name: string;
  context: string;
  driverBotId: string;
  managerBotId: string;
  deliveryPolicy: DeliveryPolicy;
  definitionYaml: string;
  yamlSummary: string[];
  /** YAML 是否已通过校验——通过后隐藏 YAML 编辑器、展示绑定面板。 */
  yamlValidated: boolean;
  templateSlot?: ReactNode;
  bindingSlot?: ReactNode;
  leaderOptions: GroupLeaderOption[];
  supportsStateMachine: boolean;
  /** 是否以任务执行(走 task execute 建群链路):仅 自定义协作(task_dag) 出现的勾选框值。 */
  viaExecute: boolean;
  onViaExecuteChange: (viaExecute: boolean) => void;
  onKindChange: (kind: GroupStrategyKind) => void;
  onNameChange: (value: string) => void;
  onContextChange: (value: string) => void;
  onDriverChange: (value: string) => void;
  onManagerChange: (value: string) => void;
  onDeliveryChange: (value: DeliveryPolicy) => void;
  onYamlChange: (value: string) => void;
}

/** 发起协作弹窗中的基础信息、协作类型和 driver/manager 配置区。 */
export function GroupConfigFields(props: GroupConfigFieldsProps) {
  const {
    kind,
    name,
    context,
    driverBotId,
    managerBotId,
    deliveryPolicy,
    definitionYaml,
    yamlSummary,
    yamlValidated,
    templateSlot,
    bindingSlot,
    leaderOptions,
    supportsStateMachine,
    viaExecute,
    onViaExecuteChange,
    onKindChange,
    onNameChange,
    onContextChange,
    onDriverChange,
    onManagerChange,
    onDeliveryChange,
    onYamlChange,
  } = props;

  return (
    <>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="mb-2 block text-xs font-semibold text-muted-foreground" htmlFor="create-group-name">
            协作群名称
          </label>
          <Input
            id="create-group-name"
            value={name}
            onChange={(event) => onNameChange(event.target.value)}
            placeholder="例如：周会协同群"
            className="h-9 rounded-md"
          />
        </div>
        <div>
          <label className="mb-2 block text-xs font-semibold text-muted-foreground" htmlFor="create-group-context">
            协作目标
          </label>
          <Input
            id="create-group-context"
            value={context}
            onChange={(event) => onContextChange(event.target.value)}
            placeholder="请输入协作目标"
            className="h-9 rounded-md"
          />
        </div>
      </div>

      <div>
        <span className="mb-2 block text-xs font-semibold text-muted-foreground" id="strategy-group-label">
          协作群类型
        </span>
        <div role="radiogroup" aria-labelledby="strategy-group-label" className="grid grid-cols-3 gap-3">
          {STRATEGY_OPTIONS.map((option) =>
            (() => {
              const disabled = option.value === 'task_dag' && !supportsStateMachine;
              return (
                <Button
                  key={option.value}
                  type="button"
                  variant="ghost"
                  role="radio"
                  aria-checked={kind === option.value}
                  aria-label={option.label}
                  disabled={disabled}
                  onClick={() => onKindChange(option.value)}
                  className={cn(
                    'h-auto flex-col items-start gap-1 rounded-lg border px-3 py-2.5 text-left',
                    disabled
                      ? 'cursor-not-allowed border-border bg-muted opacity-60'
                      : kind === option.value
                      ? 'border-primary bg-primary/10 hover:bg-primary/10'
                      : 'border-border bg-background hover:border-primary/30 hover:bg-primary/10',
                  )}
                >
                  <span
                    className={
                      disabled
                        ? 'text-xs font-semibold text-muted-foreground'
                        : kind === option.value
                        ? 'text-xs font-semibold text-primary'
                        : 'text-xs font-semibold text-foreground'
                    }
                  >
                    {option.label}
                  </span>
                  <span
                    className={cn(
                      'text-left text-[11px] leading-4',
                      disabled
                        ? 'text-muted-foreground'
                        : kind === option.value
                        ? 'text-primary'
                        : 'text-muted-foreground',
                    )}
                  >
                    {disabled ? '用户视角暂不支持创建自定义协作群' : option.description}
                  </span>
                </Button>
              );
            })(),
          )}
        </div>
      </div>

      {kind === 'task_dag' && (
        <div className="flex items-center gap-2">
          <Checkbox
            id="create-group-via-execute"
            checked={viaExecute}
            onCheckedChange={(checked) => onViaExecuteChange(checked)}
          />
          <label
            htmlFor="create-group-via-execute"
            className="cursor-pointer select-none text-[13px] text-[var(--color-muted)]"
          >
            是否以任务执行
          </label>
        </div>
      )}

      {kind === 'free_chat' && (
        <div className="grid grid-cols-2 gap-4">
          <GroupLeaderSelect
            id="create-group-driver"
            label="群主 Bot"
            value={driverBotId}
            options={leaderOptions}
            placeholder="选择群主 Bot"
            onChange={onDriverChange}
          />
          <div>
            <span className="mb-2 block text-xs font-semibold text-muted-foreground">自动回复</span>
            <Segmented<DeliveryPolicy> value={deliveryPolicy} onChange={onDeliveryChange} options={DELIVERY_OPTIONS} />
            <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
              {deliveryPolicy === 'send_to_driver'
                ? '群主 Bot 将默认回复每一条消息'
                : '群主 Bot 仅在被 @ 时或上下文高度关联时答复'}
            </p>
          </div>
        </div>
      )}

      {kind === 'task_master_slave' && (
        <div>
          <GroupLeaderSelect
            id="create-group-manager"
            label="主节点（Manager Bot）"
            value={managerBotId}
            options={leaderOptions}
            placeholder="选择 Manager Bot"
            onChange={onManagerChange}
          />
          <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
            主节点统一推进任务，其余成员作为 Worker 配合执行。
          </p>
        </div>
      )}

      {kind === 'task_dag' && !yamlValidated && (
        <div>
          {templateSlot}
          <label className="mb-2 block text-xs font-semibold text-muted-foreground" htmlFor="create-group-yaml">
            协作定义 YAML
          </label>
          <YamlCodeEditor value={definitionYaml} onChange={onYamlChange} className="text-sm" />
          <div className="mt-2 min-h-5 text-xs text-muted-foreground">
            {yamlSummary.length > 0 ? (
              <span>已识别顶层 key：{yamlSummary.join(' / ')}</span>
            ) : (
              <span>等待输入有效 YAML</span>
            )}
          </div>
          {bindingSlot}
        </div>
      )}
      {kind === 'task_dag' && yamlValidated && <div>{bindingSlot}</div>}
    </>
  );
}

export default GroupConfigFields;
