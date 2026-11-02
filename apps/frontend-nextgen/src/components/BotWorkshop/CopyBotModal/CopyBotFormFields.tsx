import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Switch } from '@/components/ui/Switch';
import { Textarea } from '@/components/ui/Textarea';
import type { BotCopyInput, BotCreateSpace } from '@/services/botWorkshop';
import { cn } from '@/utils/cn';
import type React from 'react';
import AvatarEditor from '../BotAvatar/AvatarEditor';

interface CopyBotFormFieldsProps {
  values: BotCopyInput;
  setValues: React.Dispatch<React.SetStateAction<BotCopyInput>>;
  /** 仅用于只读展示源 Bot 归属空间名;复制不允许改空间。 */
  sourceSpaces: BotCreateSpace[];
  copying: boolean;
  error?: string;
  onBack: () => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent) => void;
}

const inputFocusClass =
  'focus-visible:border-muted-foreground/40 focus-visible:ring-1 focus-visible:ring-muted-foreground/15 focus-visible:ring-offset-0 placeholder:text-muted-foreground/50';

/**
 * 复制第 2 步「重命名与确认」:样式与 CreateBotFormFields 一致,但除名称/描述外
 * 引擎/空间/服务模式/头像全部锁定只读(从源 Bot 透传),PRD:不继承协作者/外部渠道/工具授权/密钥。
 */
export const CopyBotFormFields: React.FC<CopyBotFormFieldsProps> = ({
  values,
  setValues,
  sourceSpaces,
  copying,
  error,
  onBack,
  onCancel,
  onSubmit,
}) => {
  const nameHasInvalidCharacter = values.name.includes('@');
  // 锁定字段只读展示:服务模式从源 Bot 继承,开关只显示状态。
  const sourceSpace = sourceSpaces.find((space) => space.id === values.spaceId);

  return (
    <form className="space-y-4" onSubmit={onSubmit}>
      <div className="space-y-4">
        <label className="block space-y-2 text-xs font-medium text-foreground">
          <span className="flex items-center justify-between gap-2">
            <span>
              Bot 名称 <span className="text-destructive">*</span>
            </span>
            <span className="text-[10px] font-normal text-muted-foreground">{values.name.length}/40</span>
          </span>
          <Input
            value={values.name}
            maxLength={40}
            placeholder="例如:项目知识助手"
            className={inputFocusClass}
            onChange={(event) => setValues((current) => ({ ...current, name: event.target.value }))}
            aria-invalid={Boolean(error || nameHasInvalidCharacter)}
          />
          {nameHasInvalidCharacter ? (
            <span className="block text-[10px] font-normal text-destructive">名称不能包含 @</span>
          ) : null}
        </label>

        <label className="block space-y-2 text-xs font-medium text-foreground">
          <span className="flex items-center justify-between gap-2">
            <span>描述</span>
            <span className="text-[10px] font-normal text-muted-foreground">
              {values.description.length}/200
            </span>
          </span>
          <Textarea
            rows={2}
            className={cn('min-h-[60px]', inputFocusClass)}
            value={values.description}
            maxLength={200}
            placeholder="简要说明这个 Bot 能帮助你完成什么"
            onChange={(event) => setValues((current) => ({ ...current, description: event.target.value }))}
          />
        </label>

        <div className="space-y-2 text-xs font-medium text-foreground">
          <span className="block">Bot 头像(默认继承自源 Bot,可修改)</span>
          <AvatarEditor
            value={values.avatarUrl ?? ''}
            seed={values.name.trim() || 'source-bot'}
            onChange={(avatarUrl) =>
              setValues((current) => ({ ...current, avatarUrl }))
            }
          />
        </div>
      </div>

      <div className="flex flex-col gap-2 text-xs font-medium text-foreground">
        <span id="copy-bot-engine-label" className="block">
          引擎类型(继承自源 Bot)
        </span>
        <Select value={values.engine ?? ''} disabled>
          <SelectTrigger aria-labelledby="copy-bot-engine-label">
            <SelectValue placeholder="openclaw" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={values.engine ?? 'openclaw'}>{values.engine ?? 'openclaw'}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="grid items-start gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-2 text-xs font-medium text-foreground">
          <span id="copy-bot-space-label" className="block">
            归属空间(继承自源 Bot)
          </span>
          <Select value={values.spaceId ?? ''} disabled>
            <SelectTrigger aria-labelledby="copy-bot-space-label">
              <SelectValue placeholder={sourceSpace?.name ?? '当前空间'} />
            </SelectTrigger>
            <SelectContent>
              {sourceSpace ? <SelectItem value={sourceSpace.id}>{sourceSpace.name}</SelectItem> : null}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-2 text-xs font-medium text-foreground">
          <span className="block">提供服务(继承自源 Bot)</span>
          <div className="flex h-9 w-full items-center">
            <Switch checked={values.serviceMode === 'service'} disabled aria-label="是否提供服务" />
          </div>
          <span className="block text-[10px] font-normal text-muted-foreground">
            {values.serviceMode === 'service' ? '已开启服务化' : '未开启服务化'}
          </span>
        </div>
      </div>

      <div className="flex items-center justify-between gap-3">
        {error ? (
          <p role="alert" className="m-0 min-w-0 flex-1 text-xs leading-5 text-destructive">
            {error}
          </p>
        ) : (
          <span aria-hidden className="flex-1" />
        )}
        <div className="flex shrink-0 items-center justify-end gap-2">
          <Button type="button" variant="secondary" disabled={copying} onClick={onBack}>
            上一步
          </Button>
          <Button type="button" variant="secondary" disabled={copying} onClick={onCancel}>
            取消
          </Button>
          <Button type="submit" loading={copying}>
            确认复制
          </Button>
        </div>
      </div>
    </form>
  );
};
