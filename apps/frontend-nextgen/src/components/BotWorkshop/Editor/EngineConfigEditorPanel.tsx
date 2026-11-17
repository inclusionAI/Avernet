// 引擎配置编辑面板：CodeMirror JSON 编辑器 + 保存按钮 hover diff 预览气泡。
// 交互与旧版 EngineConfigDrawer 保持一致：配置有变更时悬停保存按钮弹出变更 diff，可直接确认保存。
import { EngineConfigDiffPreview } from '@/components/BotWorkshop/Editor/EngineConfigDiffPreview';
import { JsonCodeEditor } from '@/components/BotWorkshop/Editor/JsonCodeEditor';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { BotEngineConfig } from '@/domain/botEditor';
import { useEngineConfigDiff } from '@/hooks/useEngineConfigDiff';
import { usePopoverHover } from '@/hooks/usePopoverHover';
import { RotateCcw, Save } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export function EngineConfigEditorPanel({
  config,
  savedConfig,
  editable,
  restoringDefaults,
  onConfigChange,
  onSave,
  onRestoreDefaults,
}: {
  config: BotEngineConfig;
  /** 最近一次落库成功的配置，diff 基线 */
  savedConfig: BotEngineConfig;
  editable: boolean;
  restoringDefaults?: boolean;
  onConfigChange: (value: BotEngineConfig) => void;
  onSave: () => Promise<void>;
  onRestoreDefaults: () => Promise<void>;
}) {
  const [text, setText] = useState(() => JSON.stringify(config, null, 2));
  const [error, setError] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  /** 最近一次上抛给 store 的文本（canonical 形式），用于抑制 store 回写造成的编辑器回显 */
  const liftedTextRef = useRef<string | null>(null);

  // 仅在配置被外部更新（初次加载/恢复默认/切换 Bot）时回显；用户编辑的 store 回写不打断光标
  useEffect(() => {
    const canonical = JSON.stringify(config, null, 2);
    if (liftedTextRef.current !== null && liftedTextRef.current === canonical) return;
    liftedTextRef.current = null;
    setText(canonical);
    setError('');
  }, [config]);

  const baselineText = useMemo(() => JSON.stringify(savedConfig, null, 2), [savedConfig]);
  const isDirty = text !== baselineText;

  const {
    popoverOpen,
    setPopoverOpen,
    closePopover,
    handleMouseEnterTrigger,
    handleMouseLeaveTrigger,
    handleMouseEnterContent,
    handleMouseLeaveContent,
  } = usePopoverHover({
    enabled: editable && isDirty && !isSaving,
  });

  // 气泡关闭时跳过 diff 计算，避免编辑每一笔都跑 O(n*m) 的行比较
  const { fullDiff, collapsedDiff, expandedBlocks, expandBlock, resetExpandedBlocks } = useEngineConfigDiff(
    baselineText,
    popoverOpen ? text : baselineText,
  );

  // 关闭气泡后重置折叠块展开状态
  useEffect(() => {
    if (!popoverOpen) resetExpandedBlocks();
  }, [popoverOpen, resetExpandedBlocks]);

  const handleEditorChange = useCallback(
    (value: string) => {
      setText(value);
      try {
        const parsed = JSON.parse(value) as BotEngineConfig;
        setError('');
        liftedTextRef.current = JSON.stringify(parsed, null, 2);
        onConfigChange(parsed);
      } catch {
        setError('请输入合法 JSON');
      }
    },
    [onConfigChange],
  );

  const handleSave = useCallback(async () => {
    if (Boolean(error) || !isDirty || isSaving) return;
    setIsSaving(true);
    try {
      await onSave();
      setPopoverOpen(false);
    } catch {
      // 保存失败时保持气泡打开便于核对；失败原因已由 service 层 toast
    } finally {
      setIsSaving(false);
    }
  }, [error, isDirty, isSaving, onSave, setPopoverOpen]);

  const saveDisabled = !editable || Boolean(error) || Boolean(restoringDefaults) || !isDirty || isSaving;

  return (
    <div className="flex min-h-full flex-col bg-card">
      <div className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-divider bg-card px-8 lg:px-10">
        <div className="min-w-0">
          <h2 className="m-0 truncate text-sm font-semibold">引擎配置</h2>
          <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">直接读写 Bot 草稿态的自由 JSON 配置。</p>
        </div>
        <div className="flex items-center gap-2">
          <ConfirmDialog
            title="确认恢复默认配置？"
            description="当前引擎配置将被默认配置覆盖，保存后立即生效。"
            confirmText="确认恢复"
            loading={restoringDefaults}
            disabled={!editable || Boolean(restoringDefaults)}
            onConfirm={onRestoreDefaults}
          >
            <Button
              variant="outline"
              size="sm"
              disabled={!editable || Boolean(restoringDefaults)}
              leftIcon={<RotateCcw className="size-4" />}
            >
              恢复默认配置
            </Button>
          </ConfirmDialog>
          {/* 保存按钮 + hover diff 预览气泡 */}
          <Popover open={popoverOpen} onOpenChange={setPopoverOpen}>
            <PopoverTrigger asChild>
              <div onMouseEnter={handleMouseEnterTrigger} onMouseLeave={handleMouseLeaveTrigger}>
                <Button
                  size="sm"
                  leftIcon={<Save className="size-4" />}
                  disabled={saveDisabled}
                  loading={isSaving}
                  onClick={handleSave}
                >
                  保存配置
                </Button>
              </div>
            </PopoverTrigger>
            <PopoverContent
              className="w-[560px] overflow-hidden border-slate-200 bg-white p-0 text-slate-800"
              side="top"
              align="end"
              sideOffset={8}
              onMouseEnter={handleMouseEnterContent}
              onMouseLeave={handleMouseLeaveContent}
            >
              <EngineConfigDiffPreview
                collapsedDiff={collapsedDiff}
                fullDiff={fullDiff}
                expandedBlocks={expandedBlocks}
                jsonValidation={{ valid: !error, error: error || null }}
                isSaving={isSaving}
                onExpand={expandBlock}
                onSave={handleSave}
                onCancel={closePopover}
              />
            </PopoverContent>
          </Popover>
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col px-4 py-3">
        <JsonCodeEditor value={text} onChange={handleEditorChange} editable={editable} className="min-h-0" />
        {error ? <p className="mt-2 text-xs text-[var(--color-danger)]">{error}</p> : null}
      </div>
    </div>
  );
}
