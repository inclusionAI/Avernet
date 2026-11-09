import { Button, Checkbox, Input, Switch } from '@/components/ui';
import { cn } from '@/utils/cn';
import { MessageSquare, Network, Sparkles, Workflow } from 'lucide-react';
import type { ReactNode } from 'react';
import { GroupLeaderSelect, type GroupLeaderOption } from './GroupLeaderSelect';
import { YamlCodeEditor } from './YamlEditor';

export type GroupStrategyKind = 'free_chat' | 'task_master_slave' | 'task_dag';
export type DeliveryPolicy = 'send_to_driver' | 'inject_observers';

const STRATEGY_OPTIONS: Array<{ value: GroupStrategyKind; label: string; description: string }> = [
  { value: 'free_chat', label: '自由聊天', description: '开放交流、灵活讨论和日常协作' },
  {
    value: 'task_master_slave',
    label: '任务协作',
    description: '由主节点统筹，成员分工执行',
  },
  { value: 'task_dag', label: '自定义协作', description: '通过协同剧本编排执行流程' },
];

export interface GroupConfigFieldsProps {
  fixedKind?: boolean;
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
  humanNotifySlot?: ReactNode;
  leaderOptions: GroupLeaderOption[];
  supportsStateMachine: boolean;
  /** 是否展示「是否以任务执行」勾选框（槽位 null 形态传 false 隐藏）;缺省 true 保持既有调用方行为。 */
  allowTaskExecute?: boolean;
  /** 是否以任务执行(走 task execute 建群链路):仅 自定义协作(task_dag) 出现的勾选框值。 */
  viaExecute: boolean;
  onViaExecuteChange: (viaExecute: boolean) => void;
  onKindChange: (kind: GroupStrategyKind) => void;
  onNameChange: (value: string) => void;
  onContextChange: (value: string) => void;
  /** 协作目标智能推荐是否可触发（目标为空时置灰）；不传 onRecommend 则不显示推荐按钮。 */
  canRecommend?: boolean;
  /** 点击智能推荐：以协作目标为关键词推荐可协作 Bot。 */
  onRecommend?: () => void;
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
    humanNotifySlot,
    leaderOptions,
    supportsStateMachine,
    allowTaskExecute = true,
    viaExecute,
    onViaExecuteChange,
    onKindChange,
    onNameChange,
    onContextChange,
    canRecommend,
    onRecommend,
    onDriverChange,
    onManagerChange,
    onDeliveryChange,
    onYamlChange,
  } = props;

  return (
    <>
      <div className="grid min-w-0 grid-cols-1 gap-5 sm:grid-cols-2 sm:gap-4">
        <div className="min-w-0">
          <label className="mb-2 block text-xs font-medium text-foreground" htmlFor="create-group-name">
            协作群名称
          </label>
          <Input
            id="create-group-name"
            value={name}
            onChange={(event) => onNameChange(event.target.value)}
            placeholder="例如：周会协同群"
            className="h-8 rounded-lg text-xs"
          />
        </div>
        <div className="min-w-0">
          <div className="mb-2 flex h-4 items-center justify-between gap-2">
            <label className="text-xs font-medium text-foreground" htmlFor="create-group-context">协作目标</label>
            {onRecommend && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={!canRecommend}
                onClick={onRecommend}
                aria-label="智能推荐可协作 Bot"
                className="h-6 shrink-0 gap-1 border-0 px-1 text-xs text-primary"
              >
                <Sparkles className="h-3.5 w-3.5" aria-hidden />
                智能推荐
              </Button>
            )}
          </div>
          <Input id="create-group-context" value={context} onChange={(event) => onContextChange(event.target.value)}
            placeholder="希望共同完成什么？" className="h-8 min-w-0 rounded-lg text-xs" />
        </div>
      </div>

      {!props.fixedKind && <div>
        <span className="mb-2 block text-xs font-medium text-foreground" id="strategy-group-label">
          协作群类型
        </span>
        <div role="radiogroup" aria-labelledby="strategy-group-label" className={cn('grid min-w-0 gap-2 sm:grid-cols-3 sm:gap-3', yamlValidated ? 'grid-cols-3' : 'grid-cols-1')}>
          {STRATEGY_OPTIONS.map((option) =>
            (() => {
              const disabled = option.value === 'task_dag' && !supportsStateMachine;
              const Icon = option.value === 'free_chat' ? MessageSquare : option.value === 'task_master_slave' ? Network : Workflow;
              return (
                <Button
                  key={option.value}
                  type="button"
                  variant="ghost"
                  size="sm"
                  role="radio"
                  aria-checked={kind === option.value}
                  aria-label={option.label}
                  disabled={disabled}
                  onClick={() => onKindChange(option.value)}
                  className={cn(
                    'h-auto min-w-0 items-start justify-start gap-3 whitespace-normal rounded-xl border p-3 text-left sm:flex-col sm:gap-2',
                    yamlValidated && 'items-center justify-center gap-2 px-2 py-2.5 sm:flex-row',
                    disabled
                      ? 'cursor-not-allowed border-border bg-muted opacity-60'
                      : kind === option.value
                      ? 'border-primary bg-primary/5 ring-1 ring-primary/15 hover:bg-primary/5'
                      : 'border-border bg-background hover:border-primary/40 hover:bg-muted/40',
                  )}
                >
                  <Icon aria-hidden className={cn('mt-0.5 size-4 shrink-0', yamlValidated && 'hidden sm:block', kind === option.value ? 'text-primary' : 'text-muted-foreground')} />
                  <span className="min-w-0">
                    <span className={cn('block text-xs font-medium', kind === option.value ? 'text-primary' : 'text-foreground')}>{option.label}</span>
                    {!yamlValidated && <span className="mt-1 block text-xs font-normal leading-5 text-muted-foreground">
                      {disabled ? '切换为 Bot 身份后可用' : option.description}
                    </span>}
                  </span>
                </Button>
              );
            })(),
          )}
        </div>
        {kind === 'task_dag' && allowTaskExecute && (
          <div className="mt-3 flex items-center gap-2">
            <Checkbox
              id="create-group-via-execute"
              checked={viaExecute}
              onCheckedChange={(checked) => onViaExecuteChange(checked)}
            />
            <label htmlFor="create-group-via-execute" className="select-none text-xs font-medium text-foreground">
              是否以任务执行
            </label>
          </div>
        )}
      </div>}

      {kind === 'free_chat' && (
        <div
          data-testid="free-chat-settings-grid"
          className="grid min-w-0 grid-cols-1 items-start gap-5 rounded-xl bg-muted/40 p-4 sm:grid-cols-3 sm:gap-4"
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
          <div className="min-w-0">{humanNotifySlot}</div>
          <div className="min-w-0">
            <span className="mb-2 block whitespace-nowrap text-xs font-medium text-foreground">自动回复</span>
            <div className="flex min-h-8 min-w-0 items-center gap-2">
              <Switch
                size="md"
                checked={deliveryPolicy === 'send_to_driver'}
                aria-label="自动回复"
                onCheckedChange={(checked) => onDeliveryChange(checked ? 'send_to_driver' : 'inject_observers')}
              />
              <p className="m-0 min-w-0 flex-1 break-words whitespace-normal text-xs leading-4 text-muted-foreground">
                {deliveryPolicy === 'send_to_driver'
                  ? '群主 Bot 将默认回复每一条消息'
                  : '群主 Bot 仅在被 @ 时或上下文语境高度关联时答复'}
              </p>
            </div>
          </div>
        </div>
      )}

      {kind === 'task_master_slave' && (
        <div className="grid min-w-0 grid-cols-1 gap-5 sm:grid-cols-2 sm:gap-4">
          <GroupLeaderSelect
            id="create-group-manager"
            label="主节点（Manager Bot）"
            value={managerBotId}
            options={leaderOptions}
            placeholder="选择 Manager Bot"
            onChange={onManagerChange}
          />
          <div className="min-w-0">{humanNotifySlot}</div>
        </div>
      )}

      {kind === 'task_dag' && !yamlValidated && (
        <div data-testid="custom-yaml-section" className="flex min-h-0 flex-1 flex-col">
          {templateSlot}
          <label className="mb-2 block text-xs font-medium text-foreground" htmlFor="create-group-yaml">
            协作定义 YAML
          </label>
          <YamlCodeEditor value={definitionYaml} onChange={onYamlChange} fillAvailableHeight className="text-xs" />
        </div>
      )}
      {kind === 'task_dag' && yamlValidated && <div>{bindingSlot}</div>}
    </>
  );
}

export default GroupConfigFields;
