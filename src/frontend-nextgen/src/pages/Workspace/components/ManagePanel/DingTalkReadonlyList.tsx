import type { GroupDingTalkConfig } from '@/services/workspace/channelBindingService';
import { scopeLabel, visibilityLabel } from './dingTalkConfigSelect';

function displayValue(value: unknown): string {
  return typeof value === 'string' && value.length > 0 ? value : '—';
}

export interface DingTalkReadonlyListProps {
  /** 已绑定回显配置参数；未绑定传 null，值留缺省「—」（D7 修订：不再默认为编辑态）。 */
  config: GroupDingTalkConfig | null;
}

/**
 * 钉钉配置只读字段列表（基础信息同款样式：标签上值下、长值单列、短值两列网格、
 * 无容器边框与分割线）。
 */
export function DingTalkReadonlyList({ config }: DingTalkReadonlyListProps) {
  return (
    <div className="text-xs">
      <div className="space-y-3">
        <div>
          <p className="m-0 text-muted-foreground">Robot Code</p>
          <p className="m-0 mt-1 break-all font-medium text-foreground">{displayValue(config?.robotCode)}</p>
        </div>
        <div>
          <p className="m-0 text-muted-foreground">app_key</p>
          <p className="m-0 mt-1 break-all font-medium text-foreground">{displayValue(config?.appKey)}</p>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-3">
        <div>
          <p className="m-0 text-muted-foreground">流式卡片</p>
          <p className="m-0 mt-1 font-medium text-foreground">
            {config
              ? config.enableStreamOutput
                ? `已开启 · 模板 ${displayValue(config.cardTemplateId)}`
                : '未开启'
              : '—'}
          </p>
        </div>
        <div>
          <p className="m-0 text-muted-foreground">会话模式</p>
          <p className="m-0 mt-1 font-medium text-foreground">{config ? scopeLabel(config.groupChatScope) : '—'}</p>
        </div>
        <div>
          <p className="m-0 text-muted-foreground">发送消息范围</p>
          <p className="m-0 mt-1 font-medium text-foreground">
            {config ? visibilityLabel(config.outboundVisibility) : '—'}
          </p>
        </div>
      </div>
    </div>
  );
}
