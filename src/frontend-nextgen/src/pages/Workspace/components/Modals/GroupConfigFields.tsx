import { Button, Checkbox, Input, Switch } from '@/components/ui';
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

export interface GroupConfigFieldsProps {
  kind: GroupStrategyKind;
  name: string;
  context: string;
  driverBotId: string;
  managerBotId: string;
  deliveryPolicy: DeliveryPolicy;
  definitionYaml: string;
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
      <div className="grid min-w-0 grid-cols-2 gap-3">
        <div className="min-w-0">
          <label className="mb-1.5 block text-xs font-semibold text-foreground" htmlFor="create-group-name">
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
        <div className="min-w-0">
          <label className="mb-1.5 block text-xs font-semibold text-foreground" htmlFor="create-group-context">
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
        <span className="mb-1.5 block text-xs font-semibold text-foreground" id="strategy-group-label">
          协作群类型
        </span>
        <div role="radiogroup" aria-labelledby="strategy-group-label" className="grid min-w-0 grid-cols-3 gap-3">
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
                    'h-auto min-w-0 flex-col items-start gap-1 rounded-lg border px-3 py-2.5 text-left',
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
            className="cursor-pointer select-none text-xs font-semibold text-foreground"
          >
            是否以任务执行
          </label>
        </div>
      )}

      {kind === 'free_chat' && (
        <div
          data-testid="free-chat-settings-grid"
          className="grid min-w-0 grid-cols-[minmax(180px,0.75fr)_minmax(0,1.25fr)] gap-3"
        >
          <div className="min-w-0">
            <GroupLeaderSelect
              id="create-group-driver"
              label="群主 Bot"
              value={driverBotId}
              options={leaderOptions}
              placeholder="选择群主 Bot"
              onChange={onDriverChange}
            />
          </div>
          <div className="min-w-0">
            <span className="mb-1.5 block text-xs font-semibold text-foreground">自动回复</span>
            <div className="flex min-h-9 items-center gap-3">
              <Switch
                size="md"
                checked={deliveryPolicy === 'send_to_driver'}
                aria-label="自动回复"
                onCheckedChange={(checked) => onDeliveryChange(checked ? 'send_to_driver' : 'inject_observers')}
              />
              <p className="m-0 min-w-0 text-xs leading-5 text-muted-foreground lg:whitespace-nowrap">
                {deliveryPolicy === 'send_to_driver'
                  ? '群主 Bot 将默认回复每一条消息'
                  : '群主 Bot 仅在被 @ 时或上下文语境高度关联时答复'}
              </p>
            </div>
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
        </div>
      )}

      {kind === 'task_dag' && !yamlValidated && (
        <div data-testid="custom-yaml-section" className="flex min-h-0 flex-1 flex-col">
          {templateSlot}
          <label className="mb-1.5 block text-xs font-semibold text-foreground" htmlFor="create-group-yaml">
            协作定义 YAML
          </label>
          <YamlCodeEditor value={definitionYaml} onChange={onYamlChange} fillAvailableHeight className="text-sm" />
        </div>
      )}
      {kind === 'task_dag' && yamlValidated && <div>{bindingSlot}</div>}
    </>
  );
}

export default GroupConfigFields;
