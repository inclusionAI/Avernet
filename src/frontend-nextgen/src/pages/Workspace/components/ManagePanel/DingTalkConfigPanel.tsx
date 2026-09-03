import { Badge, Button, Card, Input, Switch } from '@/components/ui';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type {
  DingTalkBindingState,
  DingTalkBindingView,
  GroupDingTalkConfig,
} from '@/services/workspace/channelBindingService';
import { DINGTALK_BINDING_CONFLICT } from '@/services/workspace/channelBindingService';
import { Bot, Info, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { ConfigSelect, SCOPE_OPTIONS, scopeLabel, VISIBILITY_OPTIONS, visibilityLabel } from './dingTalkConfigSelect';

// 兼容历史导入路径：类型源点已迁至 service 层（避免 service 反向依赖组件）。
export type { DingTalkBindingView, GroupDingTalkConfig } from '@/services/workspace/channelBindingService';

export interface DingTalkConfigPanelProps {
  canManage: boolean;
  binding: DingTalkBindingState;
  loading?: boolean;
  onSave: (config: GroupDingTalkConfig) => Promise<boolean>;
  onToggleActive: (active: boolean) => Promise<boolean>;
  onDelete: () => Promise<boolean>;
}

const EMPTY_CONFIG: GroupDingTalkConfig = {
  robotCode: '',
  appKey: '',
  appSecret: '',
  enableStreamOutput: false,
  cardTemplateId: '',
  groupChatScope: 'per_sender',
  outboundVisibility: 'full_transcript',
};

/**
 * 群绑定钉钉机器人配置面板。
 * - 未绑定：空表单，保存 → POST 创建。
 * - 已绑定：只读卡片（robotCode/appKey/流式 + 启停 + 编辑 + 解绑）；编辑 → PATCH config 全量更新。
 * - 多绑定冲突：只读提示，需联系管理员。
 * appSecret 后端不回显，新建/编辑均需用户填写。
 */
export function DingTalkConfigPanel({
  canManage,
  binding,
  loading,
  onSave,
  onToggleActive,
  onDelete,
}: DingTalkConfigPanelProps) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<GroupDingTalkConfig>(EMPTY_CONFIG);
  const [saving, setSaving] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const update = <K extends keyof GroupDingTalkConfig>(key: K, value: GroupDingTalkConfig[K]) => {
    setForm((cur) => ({ ...cur, [key]: value }));
  };

  // 类型收窄：把 'conflict' 与 null 排除，得到可编辑的绑定视图。
  const boundView: DingTalkBindingView | null = binding && binding !== DINGTALK_BINDING_CONFLICT ? binding : null;
  const isConflict = binding === DINGTALK_BINDING_CONFLICT;
  const isBound = !!boundView;

  const startEdit = () => {
    if (!boundView) return;
    // 回填非密钥字段；appSecret 恒空（后端 <redacted>），编辑时需重新输入。
    setForm({ ...EMPTY_CONFIG, ...boundView.config, appSecret: '' });
    setEditing(true);
  };

  const handleSave = async () => {
    if (!form.robotCode.trim() || !form.appKey.trim() || !form.appSecret.trim()) {
      toast.error('请完整填写 Robot Code、app_key 与 app_secret。');
      return;
    }
    if (form.enableStreamOutput && !form.cardTemplateId.trim()) {
      toast.error('启用流式卡片时需填写卡片模板 ID。');
      return;
    }
    setSaving(true);
    const ok = await onSave(form);
    setSaving(false);
    if (ok) setEditing(false);
  };

  const handleToggle = async (checked: boolean) => {
    setToggling(true);
    await onToggleActive(checked);
    setToggling(false);
  };

  const handleDelete = async () => {
    setDeleting(true);
    await onDelete();
    setDeleting(false);
  };

  const renderForm = () => (
    <div className="space-y-3">
      <label className="block">
        <span className="mb-1.5 block text-xs text-muted-foreground">Robot Code</span>
        <Input
          value={form.robotCode}
          onChange={(e) => update('robotCode', e.target.value)}
          placeholder="请输入钉钉开放平台 Robot Code"
        />
      </label>
      <label className="block">
        <span className="mb-1.5 block text-xs text-muted-foreground">app_key</span>
        <Input
          value={form.appKey}
          onChange={(e) => update('appKey', e.target.value)}
          placeholder="请输入钉钉开放平台 app_key"
        />
      </label>
      <label className="block">
        <span className="mb-1.5 block text-xs text-muted-foreground">
          app_secret{isBound ? '（更新需重新输入）' : ''}
        </span>
        <Input
          type="password"
          value={form.appSecret}
          onChange={(e) => update('appSecret', e.target.value)}
          placeholder="请输入钉钉开放平台 app_secret"
        />
      </label>
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="m-0 text-sm text-foreground">启用流式卡片</p>
          <p className="m-0 mt-0.5 text-xs text-muted-foreground">开启后使用流式卡片模板输出。</p>
        </div>
        <Switch
          checked={form.enableStreamOutput}
          onCheckedChange={(checked) => update('enableStreamOutput', checked)}
          aria-label="启用流式卡片"
        />
      </div>
      {form.enableStreamOutput && (
        <label className="block">
          <span className="mb-1.5 block text-xs text-muted-foreground">卡片模板 ID</span>
          <Input
            value={form.cardTemplateId}
            onChange={(e) => update('cardTemplateId', e.target.value)}
            placeholder="请输入流式卡片模板 ID"
          />
        </label>
      )}
      <ConfigSelect
        label="会话模式"
        value={form.groupChatScope}
        options={SCOPE_OPTIONS}
        onChange={(v) => update('groupChatScope', v)}
      />
      <ConfigSelect
        label="发送消息范围"
        value={form.outboundVisibility}
        options={VISIBILITY_OPTIONS}
        onChange={(v) => update('outboundVisibility', v)}
      />
    </div>
  );

  return (
    <Card className="rounded-lg bg-card p-3 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-primary" />
          <p className="m-0 text-sm font-medium text-foreground">钉钉机器人配置</p>
        </div>
        {loading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
        ) : isConflict ? (
          <Badge tone="warning">冲突</Badge>
        ) : boundView ? (
          <Badge tone={boundView.status === 'active' ? 'success' : 'neutral'}>
            {boundView.status === 'active' ? '已启用' : '已停用'}
          </Badge>
        ) : (
          <Badge tone="neutral">未绑定</Badge>
        )}
      </div>

      <div className="mt-3">
        {isConflict ? (
          <p className="flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            当前群存在多条钉钉绑定，请联系管理员处理后在此操作。
          </p>
        ) : boundView && !editing ? (
          <div className="space-y-3">
            <dl className="overflow-hidden rounded-xl border border-border text-xs">
              {(
                [
                  ['Robot Code', boundView.config.robotCode || '—'],
                  ['app_key', boundView.config.appKey || '—'],
                  [
                    '流式卡片',
                    boundView.config.enableStreamOutput
                      ? `已开启 · 模板 ${boundView.config.cardTemplateId || '—'}`
                      : '未开启',
                  ],
                  ['会话模式', scopeLabel(boundView.config.groupChatScope)],
                  ['发送消息范围', visibilityLabel(boundView.config.outboundVisibility)],
                ] as Array<[string, string]>
              ).map(([label, value]) => (
                <div
                  key={label}
                  className="flex items-baseline justify-between gap-3 border-b border-border px-3 py-2 last:border-b-0"
                >
                  <dt className="shrink-0 text-muted-foreground">{label}</dt>
                  <dd className="m-0 break-all text-right font-medium text-foreground">{value}</dd>
                </div>
              ))}
            </dl>
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="m-0 text-sm text-foreground">启用状态</p>
                <p className="m-0 mt-0.5 text-xs text-muted-foreground">停用后钉钉机器人不再接收本群消息。</p>
              </div>
              <Switch
                checked={boundView.status === 'active'}
                disabled={!canManage || toggling}
                onCheckedChange={handleToggle}
                aria-label="启用钉钉机器人"
              />
            </div>
            {canManage && (
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" onClick={startEdit}>
                  编辑
                </Button>
                <ConfirmDialog
                  title="解绑钉钉机器人"
                  description="解绑后本群将不再向钉钉机器人投递消息，可重新绑定。"
                  confirmText="确认解绑"
                  confirmVariant="destructive"
                  onConfirm={() => void handleDelete()}
                >
                  <Button variant="ghost" size="sm" loading={deleting} className="text-destructive">
                    解绑
                  </Button>
                </ConfirmDialog>
              </div>
            )}
          </div>
        ) : (
          <>
            {renderForm()}
            {!canManage ? (
              <p className="mt-3 flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                仅群主/驾驶位可编辑钉钉机器人配置。
              </p>
            ) : (
              <div className="mt-3 flex gap-2">
                <Button loading={saving} onClick={() => void handleSave()}>
                  {isBound ? '保存修改' : '保存绑定'}
                </Button>
                {isBound && (
                  <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
                    取消
                  </Button>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
