import { notifyError } from '@/components/ui';
import { Button } from '@/components/ui/Button';
import { Check, ImageUp, RefreshCw, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { prepareAvatar } from './prepareAvatar';
import { BOT_AVATAR_STYLE_OPTIONS, generateStyleCandidates, type StyleKey } from './avatarStyles';
import { cn } from '@/utils/cn';

export type AvatarProcessingStatus = 'idle' | 'processing' | 'failed';
export interface BotAvatarEditorProps {
  value: string;
  onChange: (url: string) => void;
  seed: string;
  disabled?: boolean;
  /** 宿主自带上传入口时隐藏本地上传槽位，避免出现双上传入口。 */
  hideUpload?: boolean;
  /** 追加到动作行的额外按钮（如内部形态的华美上传/AI 生成），换一批槽位下一行等宽展示。 */
  extraActions?: React.ReactNode;
  /** 编辑器宿主浮层（上传/AIGC 等）开合上报；宿主据此抑制外层 Popover/Dialog 的外点关闭连坐。 */
  onOverlayOpenChange?: (open: boolean) => void;
  onProcessingChange?: (status: AvatarProcessingStatus) => void;
}
type AvatarStyleTab = StyleKey | 'custom';
export default function AvatarEditor({ value, onChange, seed, disabled, hideUpload, extraActions, onProcessingChange }: BotAvatarEditorProps) {
  const [styleTab, setStyleTab] = useState<AvatarStyleTab>('bottts');
  const [batch, setBatch] = useState(0);
  const [uploadedUrl, setUploadedUrl] = useState<string>();
  const uploadRef = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const isCustomTab = styleTab === 'custom';
  // figma 稿 node 2074:9156:「Bot 头像」→ 风格 Tab 行 → 候选面板,列间距 8。
  // 候选行 = 单行 10 个 48 候选 + 行尾「换一批」48 槽,不换行;自定义 Tab 仅单张上传图
  // (稿:自定义-默认/已上传,提示文案挂槽位右侧)。
  const variants = useMemo(
    () => (styleTab === 'custom' ? [] : generateStyleCandidates(styleTab, seed, batch)),
    [styleTab, seed, batch],
  );
  // 头像兜底用机器人风格首候选,保证 value 为空、删除上传图后的回退值稳定。
  const fallbackVariant = useMemo(() => generateStyleCandidates('bottts', seed, 0)[0], [seed]);

  // 卸载时递增代次,避免在途上传结果回到已卸载的表单。
  useEffect(() => () => { generation.current += 1; }, []);

  const handleUpload = async (file?: File) => {
    if (!file) return;
    const current = ++generation.current;
    onProcessingChange?.('processing');
    try {
      const result = await prepareAvatar(file);
      if (generation.current !== current) return;
      setUploadedUrl(result);
      onChange(result);
      onProcessingChange?.('idle');
    } catch (error) {
      if (generation.current !== current) return;
      onProcessingChange?.('failed');
      notifyError(error instanceof Error ? error.message : '头像处理失败，请重新选择');
    }
  };

  const selectCandidate = (url: string) => {
    generation.current += 1;
    onChange(url);
    onProcessingChange?.('idle');
  };

  // 删除上传图:单图语义,删除后回退到机器人风格首候选。
  const handleDeleteUploaded = () => {
    generation.current += 1;
    setUploadedUrl(undefined);
    onChange(fallbackVariant);
    onProcessingChange?.('idle');
  };

  const renderTab = (key: AvatarStyleTab, label: string) => {
    const active = styleTab === key;
    return (
      <Button
        key={key}
        type="button"
        role="tab"
        aria-selected={active}
        variant="ghost"
        disabled={disabled}
        onClick={() => setStyleTab(key)}
        /* 稿 node 2074:9160(选中)/2074:9162(未选中):均 24 高胶囊、圆角 8、
           水平 padding 16;选中 = 1px 黑描边 + 12/600 文本一级,
           未选中 = 灰底(#F8F8F8→--page-canvas) + 13/400 文本二级(#525252→--content-strong)。 */
        className={cn(
          'h-6 cursor-pointer rounded-lg px-4',
          active
            ? 'border-foreground bg-transparent text-xs font-semibold text-foreground hover:bg-transparent'
            : 'bg-page-canvas text-[13px] font-normal text-content-strong',
        )}
      >
        {label}
      </Button>
    );
  };

  return (
    <div className="space-y-2">
      <input
        ref={uploadRef}
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp"
        aria-label="上传 Bot 头像"
        className="sr-only"
        disabled={disabled}
        onChange={(event) => {
          void handleUpload(event.target.files?.[0]);
          event.target.value = '';
        }}
      />
      {/* 稿 node 2074:9159:风格 Tab 行,两两间距 6。 */}
      <div className="flex items-center gap-1.5" role="tablist" aria-label="头像风格">
        {BOT_AVATAR_STYLE_OPTIONS.map((option) => renderTab(option.key, option.label))}
        {renderTab('custom', '自定义')}
      </div>
      {/* 稿 node 2074:9168:候选面板 = 灰底(#F8F8F8→--page-canvas)圆角 16 +
          1px 分割线描边(#EEEEEE→--divider) + 6px 内边距,槽间距 8,单行不换行。 */}
      <div
        data-testid="bot-avatar-candidates"
        className="flex w-full items-center gap-2 rounded-2xl border border-divider bg-page-canvas p-1.5"
      >
        {!isCustomTab ? (
          <>
            {variants.map((url, i) => {
              const selected = value === url;
              return (
                <Button
                  key={url}
                  variant="ghost"
                  type="button"
                  disabled={disabled}
                  aria-pressed={selected}
                  aria-label={`选择头像 ${i + 1}`}
                  className={cn(
                    'size-12 shrink-0 cursor-pointer overflow-visible rounded-xl bg-transparent p-0 hover:bg-transparent',
                    /* 稿 node 2074:9171:选中态 = 白底 + 1px 白环 + 3px 主色环;ring 不占布局,避免选中位移。 */
                    selected && 'bg-background ring-[3px] ring-primary ring-offset-1',
                  )}
                  onClick={() => selectCandidate(url)}
                >
                  <span className="relative block size-12">
                    <img src={url} alt="" className="size-12 rounded-xl object-cover" />
                    {/* 稿 node 2074:9173:选中 tile 右上 14×14 主色圆勾角标(icon 10)。 */}
                    {selected ? (
                      <span
                        aria-hidden
                        className="absolute right-0.5 top-0.5 flex size-3.5 items-center justify-center rounded-full bg-primary drop-shadow-[0_1px_1.5px_rgba(0,0,0,0.1)]"
                      >
                        <Check className="size-2.5" strokeWidth={3} />
                      </span>
                    ) : null}
                  </span>
                </Button>
              );
            })}
            {/* 换一批:稿 node 2074:9193,行尾 48 方槽,1px 输入框描边、圆角 12,
                icon 14 在上 + 12px 文本三级文字在下,垂直居中。 */}
            <Button
              type="button"
              variant="ghost"
              disabled={disabled}
              aria-label={`换一批，第 ${batch + 1} 批`}
              className="h-12 w-12 shrink-0 cursor-pointer flex-col gap-0 rounded-xl border border-input bg-transparent p-0 text-xs font-normal text-content-soft"
              onClick={() => setBatch((n) => n + 1)}
            >
              <RefreshCw className="size-3.5" aria-hidden />
              换一批
            </Button>
          </>
        ) : (
          <>
            {uploadedUrl ? (
              // 已上传单图:稿 node 2027:49027,槽位本身无描边;上传即当前头像。
              // hover 出半透明模糊蒙层显示「删除」(背景朦胧,沿用走查确认的删除语义)。
              <div className="group relative size-12 shrink-0 overflow-hidden rounded-xl">
                <img src={uploadedUrl} alt="已上传头像" className="size-12 rounded-xl object-cover" />
                {!disabled ? (
                  <Button
                    type="button"
                    variant="ghost"
                    aria-label="删除已上传头像"
                    onClick={handleDeleteUploaded}
                    className="absolute inset-0 h-full w-full cursor-pointer flex-col justify-center gap-0 rounded-none bg-foreground/50 p-0 text-xs font-normal text-background opacity-0 backdrop-blur-[2px] transition-opacity hover:bg-foreground/50 hover:text-background focus-visible:opacity-100 group-hover:opacity-100"
                  >
                    <Trash2 className="size-3.5" aria-hidden />
                    删除
                  </Button>
                ) : null}
              </div>
            ) : !hideUpload ? (
              // 自定义空态:稿 node 2027:26758,上传槽规格同「换一批」槽(48、1px 输入框描边、圆角 12)。
              <Button
                type="button"
                variant="ghost"
                disabled={disabled}
                onClick={() => uploadRef.current?.click()}
                aria-label="上传头像"
                className="h-12 w-12 shrink-0 cursor-pointer flex-col gap-0 rounded-xl border border-input bg-transparent p-0 text-xs font-normal text-content-soft transition-colors hover:border-primary/50 hover:bg-transparent hover:text-primary disabled:cursor-not-allowed"
              >
                <ImageUp className="size-3.5" aria-hidden />
                上传
              </Button>
            ) : null}
            {/* 稿 node 2027:48977 / 2027:49031:自定义 Tab 槽位右侧挂两行提示文案(12px 文本三级)。 */}
            <p className="m-0 shrink-0 text-xs leading-[17px] text-content-soft">
              支持从下方候选里挑选，或上传/生成新头像；
              <br />
              支持 PNG、JPEG、GIF、WebP，最大 2 MB
            </p>
          </>
        )}
      </div>
      {/* 动作行:仅渲染宿主追加入口(内部形态的华美上传/AI 生成在此);open-core 本地上传走自定义槽位。 */}
      {extraActions ? <div className="flex gap-2">{extraActions}</div> : null}
    </div>
  );
}
